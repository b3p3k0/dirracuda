# Plan: Experimental Features Dialog (rev 4)

**Date:** 2026-04-17  
**Status:** Revised — ready for approval  
**Branch:** development

---

## Context

Experimental features (Reddit Grab, Reddit Post DB) are surfaced through two scattered entrypoints — the Start Scan dialog and the Server List header. Goal: consolidate behind a single permanent `Experimental` dashboard button opening a tab-per-feature dialog, then remove legacy buttons immediately in the same pass.

Locked decisions honored:
- Button order: `[DB Tools] [Experimental] [Config]`
- Spelling: `placeholder`
- Preserve Reddit "Add to dirracuda DB" behavior
- Experimental button always visible (permanent)
- One-time warning with persisted "Don't show again"
- Legacy buttons removed immediately on wire-up (no overlap)

---

## Current-State Call Path Map

### Reddit Grab path
```
DashboardWidget._show_quick_scan_dialog
  → _d('show_unified_scan_dialog')(..., reddit_grab_callback=self._handle_reddit_grab_button_click)
    → UnifiedScanDialog._create_button_panel
        [if reddit_grab_callback is not None]: renders "Reddit Grab (EXP)" button
    → UnifiedScanDialog._open_reddit_grab
        → destroys dialog → calls reddit_grab_callback()
          → DashboardWidget._handle_reddit_grab_button_click
              → _check_external_scans(); gate: scan_button_state != "idle" → abort
              → _d('show_reddit_grab_dialog')(parent=..., grab_start_callback=_handle_reddit_grab_start)
                → DashboardWidget._handle_reddit_grab_start
                    → second gate: not idle or _reddit_grab_running → abort
                    → Thread → _reddit_grab_worker → run_ingest
                      → parent.after(0, _on_reddit_grab_done, result)
```

### Reddit Post DB path
```
ServerListWindow._create_header
  → "Reddit Post DB (EXP)" button command:
      show_reddit_browser_window(parent=self.window,
                                  add_record_callback=self.open_add_record_dialog)
        → RedditBrowserWindow.__init__ stores _add_record_callback
          → _on_add_to_db: if None → native "Not available" dialog
          → else: callback(prefill) → ServerListWindow.open_add_record_dialog
```

### Server list window tracking
```
dirracuda._open_drill_down_window
  → if "server_list": reuse or create open_server_list_window(...)
  → self.drill_down_windows['server_list'] = window
  → callback signature: (window_type, data) -> None — no return value
```

### Stable test patch targets
- `gui.components.dashboard.show_reddit_grab_dialog`
- `gui.components.dashboard.run_ingest`
- `gui.components.dashboard.threading`
- `gui.components.dashboard.messagebox`
- `gui.components.dashboard.show_unified_scan_dialog`

---

## widget.py Size — In-Card Resolution

`widget.py` is currently **1778 lines** — pre-existing violation of the >1700 stop-and-plan threshold. Resolved **within C1** via extraction to `dashboard_experimental.py`.

**Hard rule (C1 and all subsequent cards):** No non-shim logic may be added to `widget.py`. All substantive logic lives in `dashboard_experimental.py`. Reviewers should treat any logic beyond delegation lines in `widget.py` as a blocker.

Net change to `widget.py` across C1+C2: **+14 added, -1 removed = net +13**. Projected total: **~1791 lines**.

---

## C1/C2 Scope Boundary (explicit)

C1 builds the context dict with **live callbacks** from the start:
```python
context = {
    "reddit_grab_callback": widget._handle_reddit_grab_button_click,
    "open_reddit_post_db": widget._open_reddit_post_db,
}
```

Both handlers exist on `DashboardWidget` and function in C1. The reddit grab handler is fully wired immediately. The reddit post db handler degrades gracefully to the add_record_callback=None fallback in C1 because `_server_list_getter` is not yet wired in `dirracuda` (that wiring happens in C2).

**C1 DoD:** Legacy buttons (Start Scan EXP, Server List EXP) remain in place. Experimental dialog opens with both Reddit actions functional (grab fully, post-db with fallback parent). No dual-entry confusion: the two entry surfaces are independent until C2 removes the legacy ones.

**C2 DoD:** Legacy buttons removed. `_server_list_getter` wired in `dirracuda`/`gui/main.py`. Reddit Post DB opens with live `add_record_callback` when server list window is active.

This is consistent: C1 introduces the new path (with live but partially-degraded post-db), C2 completes wiring and removes the old paths in one pass.

---

## Implementation Plan

### C1 — Experimental Dialog Scaffold + Dashboard Button (includes in-card modularization)

**New module: `gui/components/dashboard_experimental.py`** (~70 lines)

Functions:
- `set_server_list_getter(widget, getter)` — stores getter on `widget._server_list_getter`
- `handle_experimental_button_click(widget)` — builds context dict with live callbacks, calls `show_experimental_features_dialog`
- `open_reddit_post_db(widget)` — acquires server list window, opens reddit browser (see contract below)

**`open_reddit_post_db` — deterministic fallback contract:**
1. `server_window = widget._server_list_getter() if widget._server_list_getter else None`
2. If `server_window` is not None, call `server_window.window.winfo_exists()`; set to None if widget is dead
3. If still None: call `widget._open_drill_down("server_list")` → re-check getter once
4. **When live:** `show_reddit_browser_window(parent=server_window.window, add_record_callback=server_window.open_add_record_dialog)`
5. **When None:** `show_reddit_browser_window(parent=widget.parent, add_record_callback=None)` — browser's native "Not available" handles UX

**`gui/dashboard/widget.py`** — shims only, zero logic:
- `__init__`: `self.experimental_button = None`, `self._server_list_getter = None` (+2)
- `_build_header_section`: pack Experimental button between DB Tools and Config; store as `self.experimental_button` (+6)
- `_refresh_theme_cached_colors`: add `experimental_button` to the button list (+1)
- `from gui.components import dashboard_experimental` (+1)
- Delegation shims (+4):
  ```python
  def set_server_list_getter(self, getter): dashboard_experimental.set_server_list_getter(self, getter)
  def _handle_experimental_button_click(self): dashboard_experimental.handle_experimental_button_click(self)
  def _open_reddit_post_db(self): dashboard_experimental.open_reddit_post_db(self)
  ```
- **Total: +14 lines** to widget.py in C1

**New files:**

`gui/components/experimental_features/__init__.py` — empty

`gui/components/experimental_features/registry.py` (~50 lines)
- `ExperimentalFeature` dataclass: `feature_id`, `label`, `build_tab(parent, context) -> tk.Widget`
- `FEATURES: list[ExperimentalFeature]`
- `build_all_tabs(notebook, context)`

`gui/components/experimental_features/reddit_tab.py` (~80 lines)
- `build_reddit_tab(parent, context) -> tk.Frame`
- Both buttons wired to live context callbacks (no stubs — context has live handlers)

`gui/components/experimental_features/placeholder_tab.py` (~40 lines)
- "Coming soon" label + scaffold note

`gui/components/experimental_features_dialog.py` (~100 lines)
- `ExperimentalFeaturesDialog(parent, context, settings_manager)`
- Warning banner with dismiss (see contract below)
- `ttk.Notebook` with registry tabs
- `[Close]` button; `ensure_dialog_focus(dialog, parent)` as final step
- Modeless (no `grab_set()`)
- `show_experimental_features_dialog(parent, context, settings_manager)` factory

**Warning-dismiss contract:**
- **Where stored:** `settings_manager.set_setting("experimental.warning_dismissed", True)` → `~/.dirracuda/gui_settings.json`
- **When read:** On `__init__` before any UI builds: `dismissed = settings_manager.get_setting("experimental.warning_dismissed", False)`
- **When written:** Immediately on `dismiss_var.trace_add("write", _on_dismiss_toggled)` — not deferred to close. Handler only writes True; never writes False.
- **Effect:** If `dismissed` is True at open time, warning frame is not built

**Validation C1:**
```bash
python3 -m py_compile gui/dashboard/widget.py \
  gui/components/dashboard_experimental.py \
  gui/components/experimental_features_dialog.py \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/reddit_tab.py \
  gui/components/experimental_features/placeholder_tab.py \
  gui/components/experimental_features/__init__.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py -q
```

**HI:** Click Experimental → tabs + warning appear. Both Reddit buttons respond (grab: opens grab dialog if idle; post-db: opens browser with fallback parent). Dismiss → reopen → no warning.

---

### C2 — Legacy Button Removal + Full Server List Wiring

**`gui/dashboard/widget.py`** (`_show_quick_scan_dialog`): Remove `reddit_grab_callback=self._handle_reddit_grab_button_click` argument (-1 line).

**`gui/components/unified_scan_dialog.py`** (`_create_button_panel`): Remove the `if self.reddit_grab_callback is not None:` EXP button block (~8 lines). Keep `reddit_grab_callback` param with default `None` — backward-compatible.

**`gui/components/server_list_window/window.py`** (`_create_header`): Remove `reddit_browser_button` block and its command closure (~10 lines). Remove `show_reddit_browser_window` import if no remaining uses in this file.

**`dirracuda`** (adjacent to `set_drill_down_callback` call):
```python
self.dashboard.set_server_list_getter(
    lambda: self.drill_down_windows.get('server_list')
)
```

**`gui/main.py`** (adjacent to `set_drill_down_callback` call in `SMBSeekGUI`): Same two-line wiring.

**Server List callback — explicit acceptance criteria:**
- **AC (positive):** Getter returns live window → `show_reddit_browser_window(parent=server_window.window, add_record_callback=server_window.open_add_record_dialog)`
- **AC (fallback):** Getter returns None after recovery → `show_reddit_browser_window(parent=widget.parent, add_record_callback=None)`. No extra error dialog from `open_reddit_post_db`.

**Validation C2:**
```bash
python3 -m py_compile gui/dashboard/widget.py dirracuda gui/main.py \
  gui/components/reddit_browser_window.py \
  gui/components/unified_scan_dialog.py \
  gui/components/server_list_window/window.py
./venv/bin/python -m pytest \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_server_list_card4.py -q
```

---

### C3 — Post-Removal Hardening (Concrete Behavioral Guards)

**C3 test 1 — scan dialog does not forward reddit callback (key-absence assertion):**

`gui/tests/test_dashboard_scan_dialog_wiring.py`:
```python
def test_show_quick_scan_dialog_does_not_pass_reddit_grab_callback(monkeypatch):
    captured = {}
    monkeypatch.setattr("gui.components.dashboard.show_unified_scan_dialog",
                        lambda **kw: captured.update(kw))
    monkeypatch.setattr("gui.components.dashboard.messagebox.showwarning",
                        lambda *a, **k: None)
    dash = _make_scan_dialog_stub()
    dash._show_quick_scan_dialog()
    # Assert the key is absent entirely — not merely None
    assert "reddit_grab_callback" not in captured
```

**C3 test 2 — server list header commands do not route to reddit browser:**

`gui/tests/test_server_list_card4.py` using existing `_import_window_module()` + `_get_window()`:
```python
def test_create_header_button_commands_do_not_open_reddit_browser(monkeypatch):
    window_mod = _get_window()
    browser_calls = []
    monkeypatch.setattr(window_mod, "show_reddit_browser_window",
                        lambda **kw: browser_calls.append(kw))

    button_commands = []

    class CommandCapturingButton:
        def __init__(self, parent, command=None, **kw):
            if command is not None:
                button_commands.append(command)
        def pack(self, **kw): pass
        def configure(self, **kw): pass
        def config(self, **kw): pass

    import tkinter as tk
    monkeypatch.setattr(tk, "Button", CommandCapturingButton)

    win = window_mod.ServerListWindow.__new__(window_mod.ServerListWindow)
    win.window = MagicMock()
    win.theme = MagicMock()
    win.theme.apply_to_widget = lambda w, s: None
    win._create_header()

    # Invoke every captured command — exercise all button actions
    for cmd in button_commands:
        try:
            cmd()
        except Exception:
            pass

    # None of the header button commands must route to show_reddit_browser_window
    assert browser_calls == []
```

This is non-trivially true: even if the command binding exists, invoking it will populate `browser_calls`. After C2 the entire button and its command are removed, so no command routes to the browser.

**C3 test 3 — experimental path reaches handlers (regression guard):**

`gui/tests/test_experimental_features_dialog.py`:
```python
def test_reddit_grab_callback_invoked_from_reddit_tab():
    handler_called = []
    context = {"reddit_grab_callback": lambda: handler_called.append(True), "open_reddit_post_db": lambda: None}
    tab_frame = MagicMock()
    tab = build_reddit_tab(tab_frame, context)
    tab._invoke_reddit_grab()   # internal trigger method; exact API determined at implementation
    assert handler_called == [True]
```

**Validation C3:**
```bash
python3 -m py_compile gui/components/unified_scan_dialog.py \
  gui/components/server_list_window/window.py \
  gui/dashboard/widget.py
./venv/bin/python -m pytest \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_server_list_card4.py \
  gui/tests/test_experimental_features_dialog.py -q
```

---

### C4 — placeholder Module Scaffold

**`experimental/placeholder/__init__.py`** (new, ~12 lines):
```python
FEATURE_ID = "placeholder"
LABEL = "placeholder"

def get_description() -> str:
    return "This tab is a scaffold for future experimental modules."
```

**`gui/components/experimental_features/placeholder_tab.py`**: Import `LABEL`, `get_description`; use in tab content.

**`gui/components/experimental_features/registry.py`**: Placeholder registered via module import.

**Validation C4:**
```bash
python3 -m py_compile experimental/placeholder/__init__.py \
  gui/components/experimental_features/placeholder_tab.py \
  gui/components/experimental_features/registry.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py -q
```

---

### C5 — Comprehensive Tests + Docs

**`gui/tests/test_experimental_features_dialog.py`** — full coverage:

**Group A: Dashboard button ordering — pack-sequence assertion**

Record `pack()` call order via a stub Button class that tracks text at pack time. Since `side=tk.LEFT` pack calls in `_build_header_section` determine render order, pack-call sequence == layout order:

```python
def test_experimental_button_packed_between_db_tools_and_config(monkeypatch):
    packed_texts = []

    class TrackingButton:
        def __init__(self, parent, text="", **kw):
            self._text = text
        def pack(self, **kw):
            packed_texts.append(self._text)
        def configure(self, **kw): pass
        def config(self, **kw): pass

    import tkinter as tk
    monkeypatch.setattr(tk, "Button", TrackingButton)
    monkeypatch.setattr(tk, "Frame", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(tk, "Label", lambda *a, **kw: MagicMock())

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = MagicMock()
    dash.theme = MagicMock()
    dash.theme.apply_to_widget = lambda w, s: None
    dash.theme.create_styled_label = lambda *a, **kw: MagicMock(pack=lambda **k: None)
    dash.theme.fonts = {"small": ("f", 9)}
    dash.main_frame = MagicMock()
    dash._theme_toggle_button_text = lambda: "☀️"
    dash._build_header_section()

    db_idx = next(i for i, t in enumerate(packed_texts) if "DB Tools" in t)
    exp_idx = next(i for i, t in enumerate(packed_texts) if "Experimental" in t)
    cfg_idx = next(i for i, t in enumerate(packed_texts) if "Config" in t)
    assert db_idx < exp_idx < cfg_idx
```

This is deterministic: pack() is called in source order, not concurrently or conditionally.

**Group B: Warning-dismiss persistence — four cases**
```python
def test_warning_shown_when_not_dismissed():
    sm = MagicMock(); sm.get_setting.return_value = False
    d = ExperimentalFeaturesDialog.__new__(ExperimentalFeaturesDialog)
    d._build_warning_section(MagicMock(), sm)
    assert d._warning_frame_built is True

def test_warning_hidden_when_already_dismissed():
    sm = MagicMock(); sm.get_setting.return_value = True
    d = ExperimentalFeaturesDialog.__new__(ExperimentalFeaturesDialog)
    d._build_warning_section(MagicMock(), sm)
    assert d._warning_frame_built is False

def test_dismiss_checkbox_writes_immediately_on_toggle():
    sm = MagicMock(); sm.get_setting.return_value = False
    d = ExperimentalFeaturesDialog.__new__(ExperimentalFeaturesDialog)
    d._build_warning_section(MagicMock(), sm)
    d.dismiss_var.set(True)
    sm.set_setting.assert_called_once_with("experimental.warning_dismissed", True)

def test_dismiss_does_not_write_false_on_uncheck():
    sm = MagicMock(); sm.get_setting.return_value = False
    d = ExperimentalFeaturesDialog.__new__(ExperimentalFeaturesDialog)
    d._build_warning_section(MagicMock(), sm)
    d.dismiss_var.set(True)
    d.dismiss_var.set(False)
    false_writes = [c for c in sm.set_setting.call_args_list if c.args[1] is False]
    assert false_writes == []
```

**Group C: Add-to-DB from Experimental path — three cases**
```python
def test_open_reddit_post_db_with_live_server_window(monkeypatch):
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = True
    dash._server_list_getter = lambda: mock_win
    calls = []
    monkeypatch.setattr("gui.components.dashboard_experimental.show_reddit_browser_window",
                        lambda **kw: calls.append(kw))
    dashboard_experimental.open_reddit_post_db(dash)
    assert calls[0]["parent"] is mock_win.window
    assert calls[0]["add_record_callback"] is mock_win.open_add_record_dialog

def test_open_reddit_post_db_fallback_when_no_server_window(monkeypatch):
    dash = _make_dash()
    dash._server_list_getter = lambda: None
    dash._open_drill_down = MagicMock()
    calls = []
    monkeypatch.setattr("gui.components.dashboard_experimental.show_reddit_browser_window",
                        lambda **kw: calls.append(kw))
    dashboard_experimental.open_reddit_post_db(dash)
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None

def test_open_reddit_post_db_treats_dead_window_as_none(monkeypatch):
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = False
    dash._server_list_getter = lambda: mock_win
    dash._open_drill_down = MagicMock()
    calls = []
    monkeypatch.setattr("gui.components.dashboard_experimental.show_reddit_browser_window",
                        lambda **kw: calls.append(kw))
    dashboard_experimental.open_reddit_post_db(dash)
    assert calls[0]["add_record_callback"] is None
```

**Docs touch targets (both required in C5):**
- `README.md`: Replace Start Scan / Server List experimental entry path with "Dashboard → Experimental → Reddit tab"
- `docs/TECHNICAL_REFERENCE.md`: Update any references to `Reddit Grab (EXP)` / `Reddit Post DB (EXP)` entrypoints

**Validation C5:**
```bash
python3 -m py_compile gui/components/experimental_features_dialog.py \
  gui/components/experimental_features/registry.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py -q
rg -n "Reddit Grab \(EXP\)|Reddit Post DB \(EXP\)" README.md docs/TECHNICAL_REFERENCE.md
```

---

### C6 — Final Validation + Evidence Report

**`docs/dev/experimental_dialog_n_features/VALIDATION_REPORT.md`** (new): Full suite evidence, line counts, manual HI PASS/FAIL/PENDING.

**Full suite:**
```bash
python3 -m py_compile \
  gui/dashboard/widget.py \
  gui/components/dashboard_experimental.py \
  gui/components/unified_scan_dialog.py \
  gui/components/server_list_window/window.py \
  gui/components/experimental_features_dialog.py \
  gui/components/experimental_features/*.py \
  experimental/placeholder/__init__.py
./venv/bin/python -m pytest \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_server_list_card4.py \
  gui/tests/test_experimental_features_dialog.py -q
```

---

## Risk List + Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| `widget.py` pre-existing >1700 violation | High | Resolved in-card via `dashboard_experimental.py`; hard rule enforced — no logic beyond shims |
| `add_record_callback` lost; wrong parent window | High | `open_reddit_post_db` uses `server_window.window` as parent when live; fallback to `widget.parent` only when None; dead-window check via `winfo_exists()`; three explicit tests in C5 Group C |
| C3 server-list test previously trivially true | Medium | Resolved: commands are captured AND invoked; `browser_calls` is populated if any command routes to reddit browser — non-trivially true post-C2 |
| C3 scan dialog None vs absent assertion | Medium | Resolved: asserts `"reddit_grab_callback" not in captured` — key-absence, not value-check |
| C5 button-order test was heuristic | Medium | Resolved: pack-sequence recording via `TrackingButton.pack()` — deterministic layout-order proof |
| C1/C2 boundary ambiguity | Low | Resolved: C1 uses live callbacks throughout; reddit post db degrades gracefully (no server_list_getter yet); legacy buttons remain until C2 removes them |
| `set_server_list_getter` not wired in `gui/main.py` | Low | Explicitly in C2; `gui/main.py` is deprecated path but must stay consistent |

---

## Manual HI Checks

1. **Button order:** `[DB Tools] [Experimental] [Config]` left-to-right in dashboard header
2. **First open:** Warning banner + "Don't show again" checkbox visible
3. **Dismiss — same session:** Check → close → reopen → no warning
4. **Dismiss — after restart:** Check → restart app → reopen Experimental → no warning
5. **Reddit Grab (idle):** Experimental → Reddit → Open Reddit Grab → grab dialog appears
6. **Reddit Grab (scanning):** During active scan → same path → grab dialog does NOT appear
7. **Reddit Post DB (server list open):** Open Server List first → Experimental → Open Reddit Post DB → browser opens as child of server list window; Add to dirracuda DB → Add Record dialog appears
8. **Reddit Post DB (server list not open):** No server list → Experimental → Open Reddit Post DB → browser opens with dashboard as parent; Add to dirracuda DB → native "Not available" info dialog
9. **No legacy button — Start Scan:** Open Start Scan → no "Reddit Grab (EXP)" button
10. **No legacy button — Server List:** Open Server List → no "Reddit Post DB (EXP)" in header
11. **Theme toggle:** Switch theme → Experimental button reflects updated colors

---

## File-Size Impact Forecast

| File | Before | After (C1+C2) | Zone |
|------|--------|---------------|------|
| `gui/dashboard/widget.py` | 1778 | **~1791** | poor (pre-existing; hard rule prevents further growth) |
| `gui/components/dashboard_experimental.py` | 0 | **~70** | excellent |
| `gui/components/unified_scan_dialog.py` | 1349 | **~1341** | good |
| `gui/components/server_list_window/window.py` | 1227 | **~1217** | excellent |
| `gui/components/reddit_browser_window.py` | 615 | **~615** | no change |
| `gui/components/experimental_features_dialog.py` | 0 | **~100** | excellent |
| `gui/components/experimental_features/registry.py` | 0 | **~50** | excellent |
| `gui/components/experimental_features/reddit_tab.py` | 0 | **~80** | excellent |
| `gui/components/experimental_features/placeholder_tab.py` | 0 | **~40** | excellent |
| `experimental/placeholder/__init__.py` | 0 | **~15** | excellent |
| `gui/tests/test_experimental_features_dialog.py` | 0 | **~200** | excellent |

---

## Blockers / Assumptions

1. `settings_manager` always set on `DashboardWidget` — confirmed in `__init__`
2. `gui/main.py` calls `set_drill_down_callback` on dashboard — confirmed; `set_server_list_getter` goes adjacent
3. `ExperimentalFeaturesDialog` is modeless; add `grab_set()` + `ensure_dialog_focus` only if modal is later required
4. `TrackingButton.pack()` order test assumes `_build_header_section` does not conditionally pack buttons or use delayed layout — confirmed by reading the existing source
