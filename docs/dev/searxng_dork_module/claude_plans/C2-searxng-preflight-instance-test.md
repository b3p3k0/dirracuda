# C2 — SearXNG Preflight (Instance Test)

## Context

C1 shipped a non-functional UI scaffold (`se_dork_tab.py`) with stub buttons and no backend wiring.  
C2 adds the preflight client (`/config` + `/search?format=json` checks), wires the Test button, persists the instance URL, and surfaces actionable reason codes including the 403/format=json hint.  
Run and Open Results buttons remain `lambda: None` stubs (C3+).

---

## Files

| Path | Action | Notes |
|---|---|---|
| `experimental/se_dork/__init__.py` | **create** | module marker |
| `experimental/se_dork/models.py` | **create** | `PreflightResult` dataclass + reason code constants |
| `experimental/se_dork/client.py` | **create** | `run_preflight()` function |
| `gui/components/experimental_features/se_dork_tab.py` | **edit** | wire Test button, load/save URL, fix description |
| `gui/components/experimental_features_dialog.py` | **edit** | inject `settings_manager` into context before `build_all_tabs` |
| `shared/tests/test_se_dork_client.py` | **create** | unit tests (urllib mocked) |
| `gui/tests/test_se_dork_tab.py` | **create** | tab method tests (no Tk required) |
| `docs/dev/searxng_dork_module/TASK_CARDS.md` | **edit** | fix typo at line 296 |

---

## Implementation Steps

### 1. `experimental/se_dork/__init__.py`
Single-line version string, same pattern as `experimental/redseek/__init__.py`.

```python
__version__ = "0.1.0"
```

---

### 2. `experimental/se_dork/models.py`
Reason code string constants + `PreflightResult` dataclass.

```python
INSTANCE_UNREACHABLE       = "instance_unreachable"
INSTANCE_FORMAT_FORBIDDEN  = "instance_format_forbidden"
INSTANCE_NON_JSON          = "instance_non_json"
SEARCH_HTTP_ERROR          = "search_http_error"
SEARCH_PARSE_ERROR         = "search_parse_error"

@dataclass
class PreflightResult:
    ok: bool
    reason_code: Optional[str]   # None when ok=True
    message: str
```

---

### 3. `experimental/se_dork/client.py`
`run_preflight(instance_url, timeout=10) -> PreflightResult`  
Uses `urllib.request` only (stdlib, no deps — same pattern as redseek client).

Two-step probe:

**Step 1 — GET `{base}/config`**  
- `URLError` → `PreflightResult(ok=False, reason_code=INSTANCE_UNREACHABLE, ...)`  
- `HTTPError` (any code) → `INSTANCE_UNREACHABLE`

**Step 2 — GET `{base}/search?q=hello&format=json`**  
- `URLError` → `INSTANCE_UNREACHABLE`  
- `HTTPError` code 403 → `INSTANCE_FORMAT_FORBIDDEN`  
  Message must include: `"Enable JSON in SearXNG settings.yml: search.formats: [html, json, csv, rss]"`  
- `HTTPError` other → `SEARCH_HTTP_ERROR`; message includes the numeric status code (e.g. `"Search endpoint returned HTTP 500"`)  
- Response body fails `json.loads` → `INSTANCE_NON_JSON`  
- Parsed JSON missing `results` key or `results` is not a list → `SEARCH_PARSE_ERROR`  
- All pass → `PreflightResult(ok=True, reason_code=None, message="Instance OK")`

URL normalization: `instance_url.rstrip('/')` before appending paths.

---

### 4. `gui/components/experimental_features/se_dork_tab.py` (edit)

**Description fix** (remove "local"):  
`"against a local SearXNG instance"` → `"against a configured SearXNG instance"`

**Settings persistence**:
- On `__init__`: read `context.get("settings_manager")`, call `sm.get_setting("se_dork.instance_url", _DEFAULT_INSTANCE_URL)` to seed `_url_var`.
- Add `_save_url()` helper: calls `sm.set_setting("se_dork.instance_url", url)` if sm is not None.

**Test button wiring** (replace `command=lambda: None`):  
- Click saves URL, disables button, sets status to `"Testing instance…"`  
- Background thread: calls `run_preflight(url)`  
- Thread posts result back via `self.frame.after(0, callback)`  
- Callback updates `_status_label` text and re-enables button  
- On `ok=True`: status = `"✓ Instance OK"` + message  
- On `ok=False`: status = `"✗ " + result.message`

Store `_test_btn` as instance attribute to allow `configure(state=...)`.

**Run / Open Results**: remain `command=lambda: None` — no change.

---

### 5. `gui/components/experimental_features_dialog.py` (minimal edit)

In `_build()`, replace:
```python
build_all_tabs(notebook, context)
```
with:
```python
tab_context = {**context, "settings_manager": settings_manager}
build_all_tabs(notebook, tab_context)
```

This is the only touch. No signature changes to `build_all_tabs`.

---

### 6. `shared/tests/test_se_dork_client.py`

Mock `urllib.request.urlopen` via `unittest.mock.patch`. Seven test cases:

| Test | Simulated condition | Expected reason_code |
|---|---|---|
| `test_preflight_success` | /config 200, /search 200 + valid JSON with `results: []` | `ok=True` |
| `test_preflight_instance_unreachable_config` | URLError on /config | `instance_unreachable` |
| `test_preflight_instance_unreachable_search` | /config 200, URLError on /search | `instance_unreachable` |
| `test_preflight_format_forbidden` | /config 200, HTTP 403 on /search | `instance_format_forbidden` |
| `test_preflight_format_forbidden_hint` | same as above | message contains `search.formats` |
| `test_preflight_search_http_error` | /config 200, HTTP 500 on /search | `search_http_error` |
| `test_preflight_non_json` | /config 200, /search 200 body = `"not-json"` | `instance_non_json` |
| `test_preflight_search_parse_error` | /config 200, /search 200, JSON missing `results` key | `search_parse_error` |

Use a context-manager-compatible mock response object with `.read()` returning bytes.

---

### 7. `gui/tests/test_se_dork_tab.py`

Use `__new__` to bypass Tk (same pattern as existing tests). All tests are unit-level with no real threads.

| Test | What it checks |
|---|---|
| `test_save_url_calls_settings_manager` | `_save_url()` calls `sm.set_setting("se_dork.instance_url", ...)` |
| `test_save_url_noop_when_no_sm` | `_save_url()` with no sm in context → no error |
| `test_invoke_test_calls_preflight` | monkeypatch `run_preflight`; call `_invoke_test_sync()` (or inline the thread call synchronously via monkeypatch of `threading.Thread`) — assert preflight was called with the URL |
| `test_invoke_test_updates_status_on_success` | success result → `_status_label` text contains "OK" |
| `test_invoke_test_updates_status_on_failure` | failure result → `_status_label` text contains message |
| `test_resolve_initial_url_uses_settings` | call pure helper `_resolve_initial_url(sm, default)` with sm returning a saved URL → returns saved URL |
| `test_resolve_initial_url_falls_back_to_default` | call `_resolve_initial_url(None, default)` → returns default |

`_resolve_initial_url(sm, default)` is a free function in `se_dork_tab.py` that takes a settings_manager (or None) and returns the URL string. It has no Tk dependency and is directly testable without `__new__`.

For threading, monkeypatch `threading.Thread` to call `target()` synchronously. Also monkeypatch `self.frame.after` to call the callback immediately.

---

### 8. `docs/dev/searxng_dork_module/TASK_CARDS.md` line 296

Change:
```
3. `docs/dev/se_dork_module/` docs pack
```
to:
```
3. `docs/dev/searxng_dork_module/` docs pack
```

---

## Settings Key

`"se_dork.instance_url"` — stored via `settings_manager.set_setting()` / `get_setting()` using the dot-path API. No DEFAULT_GUI_SETTINGS change needed; `get_setting` returns the provided default when key is absent.

---

## Verification

```bash
# Syntax check
./venv/bin/python -m py_compile \
  experimental/se_dork/client.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py

# Tests
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_client.py \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_experimental_features_dialog.py -q

# Line counts
wc -l \
  experimental/se_dork/client.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py \
  shared/tests/test_se_dork_client.py \
  gui/tests/test_se_dork_tab.py \
  docs/dev/searxng_dork_module/TASK_CARDS.md
```

Expected rubric: new files should be ≤1200 lines (excellent). `TASK_CARDS.md` is already several hundred lines and will grow by 1 character — no size concern.

---

## Risks / Assumptions

1. **`settings_manager` in context**: The dialog currently passes raw `context` to `build_all_tabs`. The edit in `experimental_features_dialog.py` injects `settings_manager` into a shallow copy — existing tests don't assert on context contents passed to `build_all_tabs`, so no regression expected.
2. **Threading in tab tests**: `threading.Thread` will be monkeypatched to run synchronously. `frame.after` will also be monkeypatched to call immediately. The tab's `frame` must exist for `after()` — tests that use `__new__` will attach a MagicMock for `frame`.
3. **`/config` response shape**: We only check reachability (HTTP 200) on `/config`, not parse its JSON. This matches the spec — the format check is on the search endpoint.
4. **HI test needed**: Yes — manual step to enter `http://192.168.1.20:8090` and click Test, verify PASS; then enter a bad URL and verify FAIL with actionable message.
