# Plan: Card V2-2 — Internal Explorer First + Fallback Prompt (B4)

## Context

`open_target()` in `explorer_bridge.py` currently calls `webbrowser.open()` directly for all resolved FTP/HTTP/HTTPS targets. D-B4 requires attempting the internal browser first; showing an explicit 3-option modal (Open in system browser / Copy address / Cancel) only when internal launch is unavailable or fails. The bridge must stay GUI-free via injected `browser_factory`.

---

## Files Changed

| File | Change |
|---|---|
| `experimental/redseek/explorer_bridge.py` | New helpers + updated `open_target` signature |
| `gui/components/reddit_browser_window.py` | Add `open_ftp_http_browser` import + factory wiring in `_on_open_explorer` |
| `shared/tests/test_redseek_explorer_bridge.py` | Replace 2 stale Group C tests + add 7 new Group C tests |
| `gui/tests/test_reddit_browser_window.py` | Fix 2 lambda signatures for new `browser_factory` kwarg |

---

## Step-by-Step Implementation

### Step 1 — `experimental/redseek/explorer_bridge.py`

**Imports to add:**
```python
import tkinter as tk
from urllib.parse import urlparse
```

**New constant:**
```python
_DEFAULT_PORTS = {"ftp": 21, "http": 80, "https": 443}
```

**New helper `_parse_for_internal(url)`:**
```python
def _parse_for_internal(url: str):
    """Parse (scheme, host, port) for internal browser launch, or None if unsupported."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    host = parsed.hostname or ""
    port = parsed.port or _DEFAULT_PORTS[scheme]
    return scheme, host, port
```

**New helper `_show_fallback_dialog(parent, url, reason)`:**
```python
def _show_fallback_dialog(parent, url: str, reason: str) -> str:
    """Blocking 3-option modal for when internal open cannot proceed.
    Returns 'browser', 'copy', or 'cancel'."""
    result = ["cancel"]
    dlg = tk.Toplevel(parent)
    dlg.title("Cannot Open Internally")
    dlg.resizable(False, False)
    dlg.grab_set()
    tk.Label(dlg, text=reason, wraplength=380, justify=tk.LEFT).pack(fill=tk.X, padx=16, pady=(12, 2))
    tk.Label(dlg, text=url, wraplength=380, justify=tk.LEFT).pack(fill=tk.X, padx=16, pady=(0, 12))
    btn_frame = tk.Frame(dlg)
    btn_frame.pack(padx=16, pady=(0, 12))
    def _pick(val):
        result[0] = val
        dlg.destroy()
    tk.Button(btn_frame, text="Open in system browser", command=lambda: _pick("browser")).pack(side=tk.LEFT, padx=(0, 6))
    tk.Button(btn_frame, text="Copy address", command=lambda: _pick("copy")).pack(side=tk.LEFT, padx=(0, 6))
    tk.Button(btn_frame, text="Cancel", command=lambda: _pick("cancel")).pack(side=tk.LEFT)
    dlg.wait_window()
    return result[0]
```

**Updated `open_target` signature and body:**
- Signature: `open_target(target, parent, *, browser_factory=None)`
- After resolving `url` (existing inference + prompt path unchanged):
  1. Call `_parse_for_internal(url)` — if None (unsupported scheme), set `reason = "Internal browser supports FTP/HTTP/HTTPS only."` and jump to step 4
  2. If `browser_factory` is not None: call `browser_factory(scheme, host, port)` — on success return; on exception set `reason = f"Internal browser failed: {exc}"`
  3. If `browser_factory` is None: `reason = "Internal browser is not available in this context."`
  4. Call `_show_fallback_dialog(parent, url, reason)` → dispatch on result: `"browser"` → `webbrowser.open(url)`, `"copy"` → `parent.clipboard_clear(); parent.clipboard_append(url)`, `"cancel"` → silent

---

### Step 2 — `gui/components/reddit_browser_window.py`

**New import at top:**
```python
from gui.components.unified_browser_window import open_ftp_http_browser
```

**Updated `_on_open_explorer`** — after building `target`, define factory and pass it:
```python
def _factory(scheme: str, host: str, port: int) -> None:
    host_type = "F" if scheme == "ftp" else "H"
    open_ftp_http_browser(
        host_type, self.window, host, port,
        scheme=scheme if host_type == "H" else None,
    )
explorer_bridge.open_target(target, self.window, browser_factory=_factory)
```
Replace the existing `explorer_bridge.open_target(target, self.window)` call.

---

### Step 3 — `shared/tests/test_redseek_explorer_bridge.py`

**Rename/update** (keep baseline URL-resolution coverage, update assertions for new behavior):
- `test_known_url_opens_directly` → rename to `test_known_url_no_factory_shows_fallback`: same http URL target, no factory → `_show_fallback_dialog` called (patches it to return "cancel"), `webbrowser.open` NOT called. Retains proof that URL was resolved correctly (passed as `url` arg to dialog).
- `test_prompt_protocol_constructs_url` → rename to `test_prompt_protocol_then_internal_launch`: patches `_ask_protocol` to return "http", provides a factory mock → factory called with `("http", "bare.host", 80)`, `webbrowser.open` NOT called. Retains proof that the constructed URL's host/port are correct.

**Keep unchanged:**
- `test_unknown_target_calls_ask_protocol` ✓ (still valid — _ask_protocol→None path unchanged)
- `test_user_cancel_does_not_open_browser` ✓ (still valid)

**New tests to add in Group C:**
1. `test_internal_launch_success` — factory provided + succeeds → `_show_fallback_dialog` NOT called, `webbrowser.open` NOT called
2. `test_no_factory_shows_fallback` — browser_factory=None for http URL → `_show_fallback_dialog` called, reason contains "not available"
3. `test_factory_failure_shows_fallback` — factory raises `RuntimeError("conn refused")` → `_show_fallback_dialog` called, reason contains "conn refused"
4. `test_fallback_browser_opens_url` — `_show_fallback_dialog` returns "browser" → `webbrowser.open` called with url
5. `test_fallback_copy_uses_clipboard` — `_show_fallback_dialog` returns "copy" → `parent.clipboard_clear()` and `parent.clipboard_append(url)` called
6. `test_fallback_cancel_is_silent` — `_show_fallback_dialog` returns "cancel" → neither `webbrowser.open` nor clipboard called
7. `test_unsupported_scheme_shows_fallback` — target_normalized="smb://host" → `_show_fallback_dialog` called with reason containing "FTP/HTTP", `webbrowser.open` NOT called (unless user picks "browser")

---

### Step 4 — `gui/tests/test_reddit_browser_window.py`

Two existing tests use `lambda t, p:` stubs for `explorer_bridge.open_target`. After V2-2, the call includes `browser_factory=_factory` as a keyword arg, which breaks the 2-arg lambda.

**`test_open_explorer_no_selection`**: change stub to `lambda t, p, **kw: open_target_calls.append(t)`

**`test_open_explorer_calls_bridge_with_correct_target`**: change stub to capture `(t, kw)` and add assertion that `kw.get("browser_factory")` is callable.

---

## Validation

```bash
# Bridge unit tests (headless)
./venv/bin/python -m pytest -q shared/tests/test_redseek_explorer_bridge.py

# Reddit browser window GUI tests
xvfb-run -a ./venv/bin/python -m pytest -q gui/tests/test_reddit_browser_window.py

# Full reddit/redseek umbrella
xvfb-run -a ./venv/bin/python -m pytest -q -k "reddit or redseek"
```

Expected: all pass, no regressions in Group A/B/C (bridge) or Group A–D (browser window).

---

## Risks

- `open_ftp_http_browser` accepts `ip_address: str` but Reddit targets are domain-based. The FTP/HTTP browser windows accept domain strings — verified by reading the function signature. No change needed.
- `urlparse("http://bare.host")` → hostname="bare.host", port=None → defaults to 80. Correct behavior.
- The `smb://` unsupported-scheme path is defensive only; current inference rules only produce ftp/http/https.
- `_show_fallback_dialog` uses `dlg.grab_set()` which requires a display. Tests must patch it — plan accounts for this.
