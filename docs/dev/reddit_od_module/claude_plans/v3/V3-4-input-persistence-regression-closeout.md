# Plan: V3-4 Input Persistence + Regression Closeout

## Context

Cards V3-1 through V3-3 added mode/search/user controls to `RedditGrabDialog`. Those inputs currently reset to hardcoded defaults every time the dialog opens. V3-4 wires `settings_manager` into the dialog so user choices are retained across sessions — matching the pattern already in use by `pry_dialog.py`, `unified_scan_dialog.py`, and others.

Settings key namespace: `reddit_grab.*`

---

## Touch Files + Line Count Check

| File | Current Lines | Est. After | Rubric |
|---|---|---|---|
| `gui/components/reddit_grab_dialog.py` | 322 | ~390 | Excellent |
| `gui/dashboard/widget.py` | 1800 (pre-existing overrun) | 1801 (+1 line only) | Poor — pre-existing, not introduced here |
| `gui/tests/test_reddit_grab_dialog.py` | 279 | ~415 | Excellent |
| `gui/tests/test_dashboard_reddit_wiring.py` | 357 | ~375 | Excellent |

> `widget.py` was already 1800 lines before this card. This card adds a single kwarg to one call — it does not introduce the overrun and does not trigger the stop-and-plan rule.

---

## Changes

### 1. `gui/components/reddit_grab_dialog.py`

Add at module level (top of file, after existing imports):

```python
import logging
_log = logging.getLogger("dirracuda_gui.reddit_grab_dialog")
```

**`__init__`** — add `settings_manager=None` param, store as `self.settings`, then call `_load_settings()` after var creation and before `tk.Toplevel`:

```python
def __init__(self, parent, grab_start_callback, settings_manager=None):
    ...
    self.settings = settings_manager
    # create all tk.StringVar / tk.BooleanVar with hardcoded defaults (unchanged)
    self._load_settings()   # overrides defaults from persisted settings
    self.dialog = tk.Toplevel(parent)
    self._build_dialog()
```

`_load_settings()` runs before `_build_dialog()`, so the initial `_on_sort_changed()` / `_on_mode_changed()` calls in `_build_dialog` pick up the loaded values.

**`_build_dialog`** — add WM_DELETE_WINDOW binding so X-button also saves:

```python
self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
```

**`_on_run`** — call `_save_settings()` after validation passes, before firing callback:

```python
def _on_run(self):
    options = self._validate()
    if options is None:
        return
    self._save_settings()
    self.grab_start_callback(options)
    self.dialog.destroy()
```

**`_on_cancel`** — call `_save_settings()` before destroy:

```python
def _on_cancel(self):
    self._save_settings()
    self.dialog.destroy()
```

**Add `_load_settings()`** — guard `if self.settings is None: return`, wrapped in `try/except Exception as e: _log.warning(...)`. Define `_coerce_bool` as a local function (same shape as `unified_scan_dialog`):

```python
def _load_settings(self) -> None:
    if self.settings is None:
        return

    def _coerce_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return default

    try:
        mode = str(self.settings.get_setting('reddit_grab.mode', 'feed'))
        if mode not in {'feed', 'search', 'user'}:
            mode = 'feed'
        self.mode_var.set(mode)

        sort = str(self.settings.get_setting('reddit_grab.sort', 'new'))
        if sort not in {'new', 'top'}:
            sort = 'new'
        self.sort_var.set(sort)

        top_window = str(self.settings.get_setting('reddit_grab.top_window', 'week'))
        if top_window not in {'hour', 'day', 'week', 'month', 'year', 'all'}:
            top_window = 'week'
        self.top_window_var.set(top_window)

        self.query_var.set(str(self.settings.get_setting('reddit_grab.query', '')))
        self.username_var.set(str(self.settings.get_setting('reddit_grab.username', '')))

        raw_max = self.settings.get_setting('reddit_grab.max_posts', 50)
        try:
            max_posts = max(1, min(200, int(raw_max)))
        except (ValueError, TypeError):
            max_posts = 50
        self.max_posts_var.set(str(max_posts))

        self.parse_body_var.set(_coerce_bool(self.settings.get_setting('reddit_grab.parse_body', True), True))
        self.include_nsfw_var.set(_coerce_bool(self.settings.get_setting('reddit_grab.include_nsfw', False), False))
        self.replace_cache_var.set(_coerce_bool(self.settings.get_setting('reddit_grab.replace_cache', False), False))
    except Exception as e:
        _log.warning("reddit_grab_dialog: failed to load settings: %s", e)
```

**Add `_save_settings()`** — guard `if self.settings is None: return`, warning-level log on failure:

```python
def _save_settings(self) -> None:
    if self.settings is None:
        return
    try:
        self.settings.set_setting('reddit_grab.mode', self.mode_var.get())
        self.settings.set_setting('reddit_grab.sort', self.sort_var.get())
        self.settings.set_setting('reddit_grab.top_window', self.top_window_var.get())
        self.settings.set_setting('reddit_grab.query', self.query_var.get())
        self.settings.set_setting('reddit_grab.username', self.username_var.get())
        raw = self.max_posts_var.get().strip()
        try:
            max_posts = max(1, min(200, int(raw)))
        except (ValueError, TypeError):
            max_posts = 50
        self.settings.set_setting('reddit_grab.max_posts', max_posts)
        self.settings.set_setting('reddit_grab.parse_body', bool(self.parse_body_var.get()))
        self.settings.set_setting('reddit_grab.include_nsfw', bool(self.include_nsfw_var.get()))
        self.settings.set_setting('reddit_grab.replace_cache', bool(self.replace_cache_var.get()))
    except Exception as e:
        _log.warning("reddit_grab_dialog: failed to save settings: %s", e)
```

Note: `bool()` is safe here for save — values come from live `tk.BooleanVar.get()`, which always returns a Python bool.

**`show_reddit_grab_dialog`** — add `settings_manager=None`, pass through:

```python
def show_reddit_grab_dialog(parent, grab_start_callback, settings_manager=None):
    dialog = RedditGrabDialog(parent, grab_start_callback, settings_manager=settings_manager)
    dialog.show()
```

---

### 2. `gui/dashboard/widget.py`

`_handle_reddit_grab_button_click` — add `settings_manager` kwarg (1 line change):

```python
_d('show_reddit_grab_dialog')(
    parent=self.parent,
    grab_start_callback=self._handle_reddit_grab_start,
    settings_manager=getattr(self, "settings_manager", None),
)
```

---

### 3. `gui/tests/test_reddit_grab_dialog.py`

Update `_make_dialog()` — add `d.settings = None`.

Add new test group `# Settings persistence`:

| Test | Covers |
|---|---|
| `test_load_settings_no_op_when_settings_none` | settings=None → vars unchanged |
| `test_load_settings_restores_mode` | mode=search loaded correctly |
| `test_load_settings_restores_sort_and_top_window` | sort=top, top_window=month |
| `test_load_settings_invalid_mode_falls_back_to_feed` | "invalid" → "feed" |
| `test_load_settings_invalid_sort_falls_back_to_new` | "hot" → "new" |
| `test_load_settings_invalid_top_window_falls_back_to_week` | "fortnight" → "week" |
| `test_load_settings_max_posts_clamped_below_min` | 0 → 1 |
| `test_load_settings_max_posts_clamped_above_max` | 999 → 200 |
| `test_load_settings_max_posts_non_integer_fallback` | "abc" → 50 |
| `test_load_settings_query_coerced_to_string` | 42 → "42" |
| `test_save_settings_no_op_when_settings_none` | no error with settings=None |
| `test_save_settings_writes_all_nine_fields` | set_setting called 9 times |
| `test_on_run_calls_save_settings_before_callback` | save order |
| `test_on_cancel_calls_save_settings` | _on_cancel persists |
| `test_mode_visibility_correct_after_settings_restore_search` | search mode shows query after load |

---

### 4. `gui/tests/test_dashboard_reddit_wiring.py`

Add one test to Group B confirming `settings_manager` kwarg is forwarded:

```python
def test_passes_settings_manager_to_dialog(self, monkeypatch):
    dash = _make_dash()
    dash.settings_manager = MagicMock()
    kwargs_seen = {}

    monkeypatch.setattr(dash, "_check_external_scans", lambda: None, raising=False)
    monkeypatch.setattr(
        "gui.components.dashboard.show_reddit_grab_dialog",
        lambda **kw: kwargs_seen.update(kw),
    )

    dash._handle_reddit_grab_button_click()

    assert kwargs_seen.get("settings_manager") is dash.settings_manager
```

---

## Ordering Note

`_load_settings()` is called after all `tk.Var` objects are created but before `tk.Toplevel` and `_build_dialog()`. This ensures:
- Loaded values are in place before widgets are created
- The initial `_on_sort_changed()` / `_on_mode_changed()` calls in `_build_dialog` see the restored state
- `top_window_var` is correctly left alone when `sort=top` (only reset when `sort=new` — intentional)

---

## Verification

```bash
# Compile check
python3 -m py_compile \
  gui/components/reddit_grab_dialog.py \
  gui/dashboard/widget.py

# Targeted suite
./venv/bin/python -m pytest \
  gui/tests/test_reddit_grab_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_experimental_features_dialog.py -q

# Full suite
./venv/bin/python -m pytest -q
```

---

## HI Test (manual)

1. Open Reddit Grab, set mode=search, query="ftp site", sort=top, top_window=month, max_posts=75. Close via Cancel.
2. Reopen — confirm all values restored.
3. Switch to mode=user, enter username, close via X button.
4. Reopen — confirm mode=user, username preserved.
5. Run one ingest and confirm no regression in result dialog or browser window.
