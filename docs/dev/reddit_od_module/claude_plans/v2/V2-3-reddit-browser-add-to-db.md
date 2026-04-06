# Plan: Card V2-3 — Reddit Browser → Add to dirracuda DB (C1, D1)

## Context

V1 of the Reddit OD module is shipped. Card V2-3 wires the Reddit browser's row data into the
existing Add Record flow in the server list window, so analysts can promote a Reddit-discovered
target into `dirracuda.db` without duplicating any DB-write logic. The bridge is callback-based
(no import coupling from Reddit module back to server list).

Locked decisions in play:
- **C1**: Same Add Record dialog, user confirms, no silent write
- **D1**: host + port only; no path/query promotion
- **Known limitation**: main DB validates `ip_address` as a literal IP; domain-based targets
  prefill the field but validation deliberately fails with a clear error message

---

## File Touch List

| File | Change type |
|------|-------------|
| `gui/components/server_list_window/actions/batch_operations.py` | Extract `_run_add_record`; add prefill+domain-note to `_show_add_record_dialog` |
| `gui/components/server_list_window/window.py` | Add `open_add_record_dialog`; pass callback to Reddit browser launch |
| `gui/components/reddit_browser_window.py` | Accept `add_record_callback`; add context menu + `_build_prefill` + `_on_add_to_db` |
| `gui/tests/test_reddit_browser_window.py` | Group E: prefill logic + callback wiring tests |
| `gui/tests/test_server_list_card4.py` | Tests for `_run_add_record` delegation + `open_add_record_dialog` |

---

## Implementation Steps

### Step 1 — `batch_operations.py`: extract `_run_add_record(prefill=None)`

**Current state:**
- `_on_add_record()` (line 34): does db check, calls `_show_add_record_dialog()`, writes to DB, refreshes

**Changes:**

a) Rename body of `_on_add_record` → new `_run_add_record(self, prefill=None)`:
   - Receives optional `prefill: dict | None`
   - Passes prefill down to `_show_add_record_dialog(prefill=prefill)` (new signature)
   - All other logic unchanged (db check, upsert, cache clear, row selection, status)

b) Replace `_on_add_record` body with a single delegate call:
   ```python
   def _on_add_record(self) -> None:
       """Open manual-add dialog and upsert one protocol row into the active database."""
       self._run_add_record()
   ```

c) Modify `_show_add_record_dialog(self, prefill=None)` signature and body:
   - After constructing all StringVars, apply prefill:
     ```python
     if prefill:
         host_type_val = prefill.get("host_type", "")
         type_var.set({"H": "HTTP", "F": "FTP"}.get(host_type_val, "SMB"))
         ip_var.set(str(prefill.get("host") or ""))
         if prefill.get("port") is not None:
             port_var.set(str(prefill["port"]))
         if prefill.get("scheme") in ("http", "https"):
             scheme_var.set(prefill["scheme"])
     ```
   - After the IP Address row in the grid, check if prefill host is a non-IP domain. If so,
     insert a warning Label (grid row increments):
     ```python
     if prefill and prefill.get("host"):
         try:
             ipaddress.ip_address(str(prefill["host"]))
         except ValueError:
             note = tk.Label(
                 form,
                 text=(
                     "Note: host appears to be a domain name.\n"
                     "The database requires an IP address — Save will\n"
                     "fail until an IP is entered."
                 ),
                 justify=tk.LEFT,
             )
             self.theme.apply_to_widget(note, "body")
             note.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
             row += 1
     ```
   - All subsequent `grid(row=row, ...)` calls already use the `row` counter, so they shift down
     automatically — no other grid indices need touching.
   - `_update_field_states()` is called after prefill is applied, ensuring field enable/disable
     state matches the prefilled type.

### Step 2 — `window.py`: public entrypoint + callback wiring

a) Add public method after `_on_add_record` delegation (near the mixin usage area):
   ```python
   def open_add_record_dialog(self, prefill=None) -> None:
       """Public entrypoint for external callers (e.g. Reddit browser) to open Add Record."""
       self._run_add_record(prefill=prefill)
   ```

b) Update `_create_header` (line 436) to pass the callback:
   ```python
   command=lambda: show_reddit_browser_window(
       parent=self.window,
       add_record_callback=self.open_add_record_dialog,
   ),
   ```

### Step 3 — `reddit_browser_window.py`: callback wiring + context menu

a) Update `__init__` signature:
   ```python
   def __init__(self, parent, db_path=None, add_record_callback=None) -> None:
       ...
       self._add_record_callback = add_record_callback
       ...
   ```

b) Update `show_reddit_browser_window` function to accept and pass `add_record_callback`:
   ```python
   def show_reddit_browser_window(parent, db_path=None, add_record_callback=None) -> None:
       RedditBrowserWindow(parent, db_path, add_record_callback=add_record_callback)
   ```

c) In `_build_window`, after the Treeview is fully configured, add context menu:
   ```python
   self._context_menu = tk.Menu(self.window, tearoff=0)
   self._context_menu.add_command(
       label="Add to dirracuda DB",
       command=self._on_add_to_db,
   )
   self.tree.bind("<Button-3>", self._on_right_click)
   ```
   Context menu is always created (no callback-gating at creation time). `_on_add_to_db` handles
   the missing-callback case at runtime with a clear info message.

d) Add `_on_right_click`:
   ```python
   def _on_right_click(self, event) -> None:
       iid = self.tree.identify_row(event.y)
       if iid:
           self.tree.selection_set(iid)
       try:
           self._context_menu.tk_popup(event.x_root, event.y_root)
       finally:
           self._context_menu.grab_release()
   ```
   Using `tk_popup` + `grab_release()` instead of `post()` avoids stuck context-menu grabs on
   some window managers.

e) Add `_build_prefill(self, row: dict) -> Optional[dict]`:
   ```python
   def _build_prefill(self, row):
       from urllib.parse import urlparse
       protocol = (row.get("protocol") or "").lower().strip()
       if protocol in ("http", "https"):
           host_type, scheme = "H", protocol
       elif protocol == "ftp":
           host_type, scheme = "F", None
       else:
           return None
       port = None
       url = row.get("target_normalized") or ""
       if url:
           try:
               port = urlparse(url).port  # None when not explicit in URL
           except Exception:
               port = None
       # Fallback: handle bare host:port form with no scheme (e.g. "192.168.1.1:8080")
       if port is None and url and "://" not in url:
           segment = url.split("/")[0]
           if ":" in segment:
               try:
                   port = int(segment.rsplit(":", 1)[1])
               except (ValueError, IndexError):
                   port = None
       return {"host_type": host_type, "host": row.get("host") or "", "port": port, "scheme": scheme}
   ```
   Note: `urlparse` is in stdlib; no new imports needed beyond adding it to the local scope.

f) Add `_on_add_to_db`:
   ```python
   def _on_add_to_db(self) -> None:
       if self._add_record_callback is None:
           messagebox.showinfo(
               "Not available",
               "Open this window from the Servers window to use 'Add to dirracuda DB'.",
               parent=self.window,
           )
           return
       row = self._selected_row()
       if row is None:
           messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
           return
       prefill = self._build_prefill(row)
       if prefill is None:
           messagebox.showinfo(
               "Cannot promote",
               f"Protocol '{row.get('protocol')}' is not supported for DB promotion.",
               parent=self.window,
           )
           return
       self._add_record_callback(prefill)
   ```

### Step 4 — Tests: `test_reddit_browser_window.py` Group E

Add a new `TestAddToDb` class covering:

1. `test_build_prefill_http` — http row → `{"host_type": "H", "scheme": "http", "port": None, ...}`
2. `test_build_prefill_https_with_explicit_port` — port extracted from URL
3. `test_build_prefill_ftp` — ftp row → `{"host_type": "F", "scheme": None, ...}`
4. `test_build_prefill_ftp_with_port` — port extracted from ftp URL
5. `test_build_prefill_unknown_protocol_returns_none` — "smb" → None
6. `test_on_add_to_db_no_callback_shows_info` — showinfo called, callback not invoked
7. `test_on_add_to_db_no_selection_shows_info` — showinfo, no callback
8. `test_on_add_to_db_unknown_protocol_shows_info` — prefill=None path
9. `test_on_add_to_db_calls_callback_with_correct_prefill` — happy path with http row

All use the headless `_make_win()` helper + `_CaptureTree`.  
`_make_win()` needs `win._add_record_callback = None` default in its body.

### Step 5 — Tests: `test_server_list_card4.py` additions

Add two new test classes (append to existing file):

**`TestRunAddRecordDelegation`** (uses `_BatchMixinStub` pattern from `test_action_routing.py`,
reloaded with the same isolated import pattern already in the file):

1. `test_on_add_record_delegates_to_run_add_record` — monkeypatch `_run_add_record`, call
   `_on_add_record`, assert delegate called with no args
2. `test_run_add_record_no_db_shows_error` — `db_reader=None`, assert showerror called
3. `test_run_add_record_cancel_returns_no_db_write` — dialog returns `None`, assert upsert not called
4. `test_run_add_record_with_prefill_passes_to_dialog` — call `_run_add_record(prefill={...})`,
   verify `_show_add_record_dialog` receives `prefill=` kwarg

**`TestOpenAddRecordDialogMethod`** (uses `_import_window_module()` already in file):

1. `test_open_add_record_dialog_calls_run_add_record` — monkeypatch `_run_add_record`, call
   `open_add_record_dialog()`, assert called once with `prefill=None`
2. `test_open_add_record_dialog_passes_prefill` — call `open_add_record_dialog(prefill={...})`,
   assert `_run_add_record` receives it

These tests require no Tk window (use `__new__` bypass).

---

## Prefill Mapping Rules (Locked)

| Reddit target `protocol` | `host_type` | `scheme` | `port` | Source |
|--------------------------|-------------|----------|--------|--------|
| `http`                   | `"H"`       | `"http"` | `urlparse(target_normalized).port` or `None` | D1 |
| `https`                  | `"H"`       | `"https"`| same   | D1 |
| `ftp`                    | `"F"`       | `None`   | same   | D1 |
| anything else            | n/a → `None` result → showinfo | — | — | D1 |

`host` = `row["host"]` (stored in sidecar DB at parse time).  
Path and query components of `target_normalized` are **not** promoted (D1).

---

## Risks

1. **Domain-host UX**: The warning label is informational; validation failure is via
   `_normalize_manual_record_input` raising `ValueError → messagebox.showerror`. This is
   intentional per locked decision. No silent write, no bypass.

2. **`_show_add_record_dialog` grid row counter**: The warning label inserts at `row` and
   increments it. All subsequent `grid(row=row, ...)` calls use the running counter, so they
   shift correctly. The buttons frame uses `row + 1` at the end — verify this still works when
   warning label is not shown (counter unchanged).

3. **`show_reddit_browser_window` is a public function**: Adding `add_record_callback` is
   backward-compatible (keyword-only with default `None`). Existing callers outside
   `window.py` (e.g. start scan dialog if any) are unaffected.

4. **Context menu dismissal**: `_on_right_click` always posts the menu. If user right-clicks
   on empty space below rows, `identify_row` returns `""` — selection is not changed but menu
   still appears. `_on_add_to_db` then hits the "No selection" path cleanly.

---

## Validation

```bash
# Primary
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests/test_reddit_browser_window.py
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests/test_server_list_card4.py

# Regression
xvfb-run -a ./venv/bin/python -m pytest -q -k "reddit or redseek"
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests/test_action_routing.py
```

Manual HI gate (Flow C in validation plan):
1. Right-click Reddit row → "Add to dirracuda DB" → dialog opens prefilled
2. IP target: save succeeds, row appears in server list
3. Domain target: dialog shows domain warning label; Save triggers "Invalid IP Address" error;
   dialog stays open; no write occurs
