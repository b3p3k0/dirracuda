# V2-0 Plan: Reality Check — File Touch Map, Risks, Gates

**Card:** V2-0 (plan only, no code changes)  
**Date:** 2026-04-05  
**Locked decisions applied:** A2, B4, C1, D1

---

## Context

V1 ships the Reddit OD module as a working experimental standalone. V2 makes it operationally useful:
- A2: target rows carry readable preview text (notes column)
- B4: internal FTP/HTTP browsers open first; system browser is the fallback
- C1/D1: right-click from Reddit browser promotes host:port into main DB via existing Add Record flow
- V2-4: README entry-points added for new operators

This planning card confirms exact touch lists and surfaces risks before any code is written.

---

## Current State: Key Observations

### notes field
- `RedditTarget.notes` field exists in `experimental/redseek/models.py`
- `upsert_targets` in `store.py` writes `t.notes` to the DB (line ~257)
- **Currently set by parser.py only:** `notes="truncated"` when source exceeds 100KB, else `None`
- `service.py` does NOT touch `notes` — passes targets straight from `extract_targets()` to `upsert_targets()`
- D-A2 explicitly says: "parser diagnostic note text is not retained in stored rows" — V2-1 overwrites parser's value with the preview

### explorer_bridge.py
- `open_target()` calls `webbrowser.open()` directly today — system browser is default (wrong for B4)
- Bridge receives `(target: RedditTarget, parent: tk.Widget)` — parent is already available
- `FtpBrowserWindow` / `HttpBrowserWindow` live in `gui/components/unified_browser_window.py`
- Bridge is under `experimental/redseek/` — importing GUI classes there would create a cross-layer dependency. **Preferred pattern: inject a `browser_factory` callable from `reddit_browser_window.py`** to keep the bridge decoupled

### Add Record dialog
- `_show_add_record_dialog()` in `gui/components/server_list_window/actions/batch_operations.py` has **no prefill support** — all `StringVar` start empty
- `_normalize_manual_record_input()` validates `ip_address` as a literal IPv4/IPv6 — **domain-based Reddit targets will fail this validation**
- No context menu exists in `reddit_browser_window.py` — only button-based actions

---

## 1. Exact File Touch List

### V2-1 (A2 — Notes Preview)
| File | Change type |
|---|---|
| `experimental/redseek/service.py` | Add `_make_preview_note(title, body)` helper; call it per target in `_run_new` and `_run_top` before `upsert_targets` |
| `shared/tests/test_redseek_service.py` | Add tests: normal preview, body omitted when parse-body off, truncation at 120 chars |

### V2-2 (B4 — Internal Explorer First)
| File | Change type |
|---|---|
| `experimental/redseek/explorer_bridge.py` | Replace `open_target()` body: try internal launch via injected factory; on failure show 3-option fallback prompt |
| `gui/components/reddit_browser_window.py` | Pass `browser_factory` callable into `open_target()` call |
| `shared/tests/test_redseek_explorer_bridge.py` | Add tests: internal success, internal failure → fallback, copy-address, cancel |

### V2-3 (C1, D1 — Context Menu Add to DB)
| File | Change type |
|---|---|
| `gui/components/reddit_browser_window.py` | Add right-click binding + context menu (`_create_context_menu`, `_on_add_to_db`); accept `add_record_callback` kwarg in `__init__` |
| `gui/components/server_list_window/actions/batch_operations.py` | Extract `_run_add_record(prefill=None)` shared helper; `_on_add_record()` delegates to it; add prefill support + domain-host UX guidance to `_show_add_record_dialog()` |
| `gui/components/server_list_window/window.py` | Expose public `open_add_record_dialog(prefill=None)` → calls `self._run_add_record(prefill=prefill)`; pass `add_record_callback=self.open_add_record_dialog` when instantiating `reddit_browser_window` (line ~433) |
| `gui/tests/test_reddit_browser_window.py` | Add context menu construction test, `_on_add_to_db` with selection test, no-selection no-op test |
| `gui/tests/test_server_list_card4.py` | Add prefill-dialog tests: HTTP prefill, FTP prefill, domain-host guidance path, cancel path |

### V2-4 (README + Validation Refresh)
| File | Change type |
|---|---|
| `README.md` | Add "Reddit experimental actions" section with exact click paths |
| `docs/dev/reddit_od_module/12-V2_VALIDATION_PLAN.md` | Update status labels to PENDING for V2 flows after implementation |

---

## 2. Step-by-Step Implementation Plan Per Card

### Card V2-1: Target Notes Preview Capture

**Step 1 — Add helper `_make_preview_note(title, body)` in `service.py`**
- Normalize whitespace on title and body independently (`" ".join(s.split())`)
- Truncate each to 120 chars
- Build output: `f"T:{title_preview}"` if title non-empty; append `f" | B:{body_preview}"` if body non-empty
- Return the combined string (or `None` if both empty, though this should be rare)

**Step 2 — Apply preview to each target before upsert in `_run_new` and `_run_top`**
- After `extract_targets()` returns targets, iterate and set `target.notes = _make_preview_note(post.title, post.body)` for each
- `post.title` and `post.body` are already in scope at both `upsert_targets` call sites
- This overwrites any parser diagnostic value (e.g., `"truncated"`) per D-A2 intent

**Step 3 — Add tests in `test_redseek_service.py`**
- `test_preview_note_title_and_body` — stored row has `T:... | B:...` format
- `test_preview_note_body_omitted_when_parse_body_off` — body preview absent when parse-body flag false or body None
- `test_preview_note_120_char_truncation` — titles/bodies longer than 120 chars are capped
- `test_preview_note_whitespace_normalized` — multiple spaces/newlines collapsed

### Card V2-2: Internal Explorer First + Fallback Prompt

**Step 1 — Extend `open_target()` signature**
- Add optional param: `browser_factory: Optional[Callable] = None`
- Signature: `open_target(target, parent, browser_factory=None)`

**Step 2 — Attempt internal launch**
- If `browser_factory` is provided and inferred protocol is `ftp`/`http`/`https`: call `browser_factory(protocol, host, port, parent)` inside a `try/except`
- If that call succeeds (no exception): return (done)
- If it raises or `browser_factory` is None: proceed to fallback

**Step 3 — Fallback 3-option prompt**
- Build a small custom `tk.Toplevel` modal (not `messagebox` — labels must match exactly):
  - Label: brief failure reason (e.g., "Could not open internally: `<exc>`")
  - Button: `Open in system browser` → `webbrowser.open(url)`; destroy dialog
  - Button: `Copy address` → `parent.clipboard_clear(); parent.clipboard_append(url)` (Tk clipboard only — no pyperclip dep); destroy dialog
  - Button: `Cancel` → destroy dialog silently

**Step 4 — Update `reddit_browser_window.py` call site**
- In `_on_open_explorer()`, pass a `browser_factory` lambda that calls the existing `open_ftp_http_browser(...)` launcher (not direct `FtpBrowserWindow`/`HttpBrowserWindow`) so behavior stays consistent with the existing browser launch path
- Pass factory into `explorer_bridge.open_target(target, self.window, browser_factory=factory)`

**Step 5 — Tests in `test_redseek_explorer_bridge.py`**
- `test_internal_launch_success_does_not_open_webbrowser` — factory called, webbrowser not called
- `test_internal_launch_failure_shows_fallback_prompt` — factory raises, custom Toplevel dialog is built
- `test_fallback_copy_address_writes_tk_clipboard` — copy button calls `clipboard_clear` + `clipboard_append` on parent (assert via monkeypatched parent); no pyperclip
- `test_fallback_cancel_silent` — cancel destroys dialog, no side effects

### Card V2-3: Reddit Browser Context Menu → Add to dirracuda DB

**Step 1 — Add right-click context menu in `reddit_browser_window.py`**
- In `__init__`, create `self._context_menu = tk.Menu(self.window, tearoff=0)`
- Add command: `Add to dirracuda DB` → `self._on_add_to_db`
- Bind `<Button-3>` (right-click, Linux) on the treeview to `self._on_context_menu`
- `_on_context_menu(event)`: select row under cursor, then `tk_popup` the menu

**Step 2 — Add `_on_add_to_db()` in `reddit_browser_window.py`**
- Get selected row data (host, protocol, port from `target_normalized` or `host`/`protocol` fields)
- Extract host and port per D1 mapping: http/https → HTTP fields; ftp → FTP fields
- Call `self._add_record_callback(prefill={...})` (injected at construction)
- Prefill dict shape: `{"type": "H"/"F", "host": host, "port": port, "scheme": scheme}`

**Step 3 — Extract `_run_add_record(prefill=None)` shared helper in `batch_operations.py`**
- Move the canonical flow (dialog → normalize → upsert → refresh/status) into `_run_add_record(prefill=None)` on the mixin
- `_on_add_record()` (existing call site) becomes: `self._run_add_record()`
- No logic duplication between Server List path and Reddit callback path

**Step 4 — Expose `open_add_record_dialog(prefill=None)` on `server_list_window/window.py`**
- Public method: `self._run_add_record(prefill=prefill)` — one line
- This is the callable passed as `add_record_callback` when the Server List instantiates `reddit_browser_window`
- Explicitly pass it at the `reddit_browser_window` instantiation site (~line 433 of `window.py`): `add_record_callback=self.open_add_record_dialog`

**Step 5 — Add prefill support to `_show_add_record_dialog()` in `batch_operations.py`**

Prefill payload schema (locked):
```
{
  "host_type": "H" | "F"           # str, required; aligns with normalize output contract
  "host":      str                  # str, required; mapped to ip_var StringVar
  "port":      int | None           # int or None
  "scheme":    "http" | "https" | None  # str or None; HTTP only
}
```
- Add `prefill: Optional[dict] = None` parameter (default preserves existing call sites)
- If prefill provided: init `StringVar`s from `prefill["host"]` → `ip_var`, `prefill["port"]` → `port_var`, etc.
- Map `prefill["host_type"]` to the type dropdown (`"H"` → `"HTTP"`, `"F"` → `"FTP"`)
- Add non-IP host guidance: if `prefill["host"]` is not a valid IP, show inline label in dialog: "Note: domain-based host — validation will require a resolvable address"
- `_normalize_manual_record_input()` already rejects non-IPs; do NOT bypass — surface error in dialog, block Save

**Step 6 — Wire `add_record_callback` injection in `reddit_browser_window.py`**
- Accept `add_record_callback: Optional[Callable] = None` in `__init__` (default None for tests/standalone)
- Store as `self._add_record_callback`
- In `_on_add_to_db`: guard `if not self._add_record_callback: return`

**Step 7 — Tests**
- `test_reddit_browser_window.py`: `test_context_menu_created`, `test_context_menu_no_selection_noop`, `test_on_add_to_db_calls_callback_with_prefill`
- `test_server_list_card4.py`: `test_add_record_dialog_prefill_http`, `test_add_record_dialog_prefill_ftp`, `test_add_record_dialog_domain_host_shows_guidance`, `test_add_record_dialog_prefill_cancel_returns_none`
- `test_server_list_card4.py`: `test_on_add_record_delegates_to_run_add_record` — verify no logic lives in `_on_add_record` that isn't in `_run_add_record`

### Card V2-4: README Entry-Point Clarity

**Step 1 — Add "Reddit Experimental Actions" section to `README.md`**
- Under a new subsection (near existing GUI/workflow section)
- Click path 1: `Start Scan dialog → Reddit Grab (EXP)` button
- Click path 2: `Servers window → Reddit Post DB (EXP)` button
- Keep wording operator-facing (2–3 lines per path max)
- Do NOT restructure existing README sections

**Step 2 — Update validation plan status labels in `12-V2_VALIDATION_PLAN.md`**
- Mark flows A–D as `MANUAL: PENDING` (they are not yet run)
- Mark automated suites as `AUTOMATED: PENDING`

---

## 3. Risks, Bad Assumptions, Likely Regressions

### V2-1

| Risk | Severity | Notes |
|---|---|---|
| Parser sets `notes="truncated"` on large-body targets — V2-1 will silently overwrite this | Low | D-A2 explicitly accepts this; diagnostic value is lost but intentionally |
| `post.body` may be `None` or `"[deleted]"` in both `_run_new` and `_run_top` — preview helper must guard | Medium | Use `body or ""` and check after normalization |
| Targets extracted with `extract_targets(post, parse_body=False)` — `post.body` may not be passed into extracted target objects | Medium | Preview helper uses `post.title`/`post.body` from the parent post object in service.py, not from the target — this is correct but needs verification that `post.body` is accessible at both upsert sites |
| `INSERT OR IGNORE` in `upsert_targets` means **existing rows are never updated** — if a target was already inserted with `notes=None` in V1, re-running will NOT populate its preview | High | V2-1 only populates notes for new inserts. Old rows require a migration or manual re-scan. Flag this in the handoff. |

### V2-2

| Risk | Severity | Notes |
|---|---|---|
| `FtpBrowserWindow`/`HttpBrowserWindow` constructors expect `ip_address` — domain names may be passed | Medium | Parameter is named `ip_address` but likely works with hostnames; test with a domain target before shipping |
| `FtpBrowserWindow`/`HttpBrowserWindow` may attempt a network connection during `__init__` — blocking the UI thread | High | Verify if connection is deferred or immediate. If immediate, the factory must be called in a thread, complicating the fallback prompt flow |
| Importing GUI components from `experimental/redseek/` would create a cross-layer dependency | High | **Use factory injection pattern** — bridge stays decoupled; caller provides the factory |
| Existing `test_open_explorer_calls_bridge_with_correct_target` in `test_reddit_browser_window.py` will need updating to pass `browser_factory` | Low | Straightforward update but must not be forgotten |
| All 4 `open_target` tests in Group C of `test_redseek_explorer_bridge.py` will break (signature change) | Medium | Update all to pass `browser_factory=None` explicitly |

### V2-3

| Risk | Severity | Notes |
|---|---|---|
| `add_record_callback` not wired at reddit_browser_window instantiation site in `window.py` (~line 433) | **High** | Must pass `add_record_callback=self.open_add_record_dialog` explicitly; omitting it leaves the action a silent no-op |
| `_normalize_manual_record_input()` rejects non-IP hosts — domain-based Reddit targets will silently fail | **Critical** | Must surface clear UX guidance BEFORE user clicks Save; do not silently discard or bypass validation |
| `_show_add_record_dialog()` is used by Server List's own `_on_add_record()` — adding prefill param must be backwards-compatible (default `None` = existing behavior) | Medium | Use `prefill=None` default; gate all prefill logic behind `if prefill` |
| Right-click on treeview: Linux uses `<Button-3>`, macOS uses `<Button-2>` — current project appears Linux-primary | Low | Check if app has platform normalization; if not, use `<Button-3>` and document |
| Logic drift if `open_add_record_dialog()` duplicates `_on_add_record()` flow | **High** | Resolved by extracting `_run_add_record(prefill=None)` in the mixin; both call sites delegate to it — no copied logic |
| `test_dashboard_reddit_wiring.py` (listed in validation plan) may not exist yet — if it doesn't, V2 automated suite B will fail at the import level | Low | Check if this file exists before running validation suite B |

### V2-4

| Risk | Severity | Notes |
|---|---|---|
| README click-path text may not match actual button labels if they change between now and docs update | Low | Verify exact button label strings from `reddit_browser_window.py` and the scan dialog before writing |
| Broad README restructuring risk is minimal — V2-4 scope is strictly additive | Low | Confirmed by card scope |

---

## 4. Validation Commands with Expected Outcomes

### V2-1
```bash
# Unit tests only (no GUI, no network)
./venv/bin/python -m pytest -v shared/tests/test_redseek_service.py
```
Expected: all existing tests pass + new preview tests pass; no `notes`-related failures.

```bash
# Quick smoke: notes format in DB after ingest
./venv/bin/python -m pytest -v -k "preview_note" shared/tests/test_redseek_service.py
```
Expected: 4 new tests collected and passing.

### V2-2
```bash
./venv/bin/python -m pytest -v shared/tests/test_redseek_explorer_bridge.py
```
Expected: all original tests still pass + 4 new bridge tests pass.

```bash
# Regression: reddit browser window unaffected
xvfb-run -a ./venv/bin/python -m pytest -v gui/tests/test_reddit_browser_window.py
```
Expected: `test_open_explorer_calls_bridge_with_correct_target` passes (updated signature), no new failures.

### V2-3
```bash
xvfb-run -a ./venv/bin/python -m pytest -v \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_server_list_card4.py
```
Expected: all existing tests pass + new context-menu and prefill tests pass.

```bash
# Regression: server list unaffected
xvfb-run -a ./venv/bin/python -m pytest -v -k "server_list" gui/tests/
```
Expected: no regressions in server list wiring.

### V2-4
```bash
# Docs sanity: no broken references
grep -n "Reddit Grab" README.md
grep -n "Reddit Post DB" README.md
```
Expected: both strings appear with click-path context.

### Full V2 suite
```bash
# Redseek core
./venv/bin/python -m pytest -q \
  shared/tests/test_redseek_service.py \
  shared/tests/test_redseek_explorer_bridge.py

# Reddit browser + server list integration
xvfb-run -a ./venv/bin/python -m pytest -q \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_server_list_card4.py

# Browser regression confidence
xvfb-run -a ./venv/bin/python -m pytest -q \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py

# Umbrella
xvfb-run -a ./venv/bin/python -m pytest -q -k "reddit or redseek"
```

---

## 5. PASS/FAIL Gates Per Card

### V2-1 PASS criteria
- [ ] `test_redseek_service.py` — all original tests PASS
- [ ] New preview tests: truncation at 120, whitespace normalization, body-omit when None, `T:... | B:...` format — all PASS
- [ ] Manual: run Reddit Grab, open Reddit Post DB, confirm `notes` column shows deterministic preview; no `"truncated"` string visible from parser diagnostic
- [ ] **Required card output:** result/handoff must include explicit note — "V1 rows without preview notes require a replace-cache re-run to populate; no automatic backfill"
- [ ] FAIL if: old-row backfill is silently attempted (must not be)

### V2-2 PASS criteria
- [ ] `test_redseek_explorer_bridge.py` — all original + new tests PASS
- [ ] `test_reddit_browser_window.py` — `test_open_explorer_calls_bridge_with_correct_target` PASS (updated)
- [ ] Manual: select HTTP/FTP target → `Open in Explorer` → internal browser window opens
- [ ] Manual: force fallback (unsupported target) → 3-option prompt appears; all three choices behave as labeled
- [ ] FAIL if: system browser opens before internal is attempted

### V2-3 PASS criteria
- [ ] `test_reddit_browser_window.py` — new context-menu tests PASS
- [ ] `test_server_list_card4.py` — new prefill + domain-guidance tests PASS
- [ ] All existing server list + reddit browser tests still PASS
- [ ] Manual: right-click row → `Add to dirracuda DB` → prefilled dialog opens → user confirms → row appears in Server List under correct protocol
- [ ] Manual: domain-based host target → dialog shows guidance text, Save blocked (no silent write)
- [ ] FAIL if: `_show_add_record_dialog()` called without `prefill=None` default breaks existing Server List path

### V2-4 PASS criteria
- [ ] `README.md` contains exact click paths for both Reddit EXP actions
- [ ] README change is additive only — no existing sections modified
- [ ] Manual: new operator can locate both Reddit actions from README alone
- [ ] FAIL if: README click-path text does not match actual button label strings in the UI

### Overall V2 PASS (exit criterion)
- [ ] All automated suites: `AUTOMATED: PASS`
- [ ] Manual flows A–D: `MANUAL: PASS`
- [ ] No scan_manager/backend CLI coupling introduced
- [ ] No new columns added to `reddit_targets` or `dirracuda.db` schemas
- [ ] No commits unless explicitly requested
