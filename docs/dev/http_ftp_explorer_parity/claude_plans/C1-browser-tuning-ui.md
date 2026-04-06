# C1: Shared Tuning Surface + Persistence Plumbing

## Context

FTP/HTTP explorers use `UnifiedBrowserCore` for shared UI but currently expose no download tuning controls. SMB already has worker count + large-file threshold spinboxes and full settings-manager persistence. C1 closes the UI parity gap: add the tuning strip to FTP/HTTP windows, wire persistence to the shared `file_browser.*` settings keys, and show the large-file control as visible-but-disabled for HTTP with an inline explanation. No runtime behavior changes (C2).

## Key Observations

- `SmbBrowserWindow._build_window()` **overrides** `UnifiedBrowserCore._build_window()`, so any change to the base `_build_window()` has zero SMB impact.
- `SmbBrowserWindow._persist_tuning()` is defined on the SMB class (line 2780); FTP/HTTP need their own copy on `UnifiedBrowserCore`.
- FTP/HTTP configs (`_load_ftp_browser_config`, `_load_http_browser_config`) do not include `download_worker_count` or `download_large_file_mb` — defaults (2 workers, 25 MB) are hardcoded in init.
- Both FTP and HTTP `__init__` already hold `self.settings_manager` and call `self._build_window()` near the end — tuning init goes just before `_build_window()`.

## Files to Change

- `gui/components/unified_browser_window.py`
- `gui/tests/test_ftp_browser_window.py`
- `gui/tests/test_http_browser_window.py`

## Implementation Steps

### 1. Add adapter hook + `_persist_tuning` to `UnifiedBrowserCore` (lines ~226–240, ~517–537)

Add to the adapter-hooks section:
```python
def _adapt_large_file_tuning_enabled(self) -> bool:
    """Return True if large-file threshold spinbox should be active. HTTP overrides to False."""
    return True
```

Add to the status/button helpers section (before the closing of the class):
```python
def _persist_tuning(self) -> None:
    try:
        self.download_workers = max(1, min(3, int(self.workers_var.get())))
        self.download_large_mb = max(1, int(self.large_mb_var.get()))
    except Exception:
        return
    if self.settings_manager:
        try:
            self.settings_manager.set_setting("file_browser.download_worker_count", self.download_workers)
            self.settings_manager.set_setting("file_browser.download_large_file_mb", self.download_large_mb)
        except Exception:
            pass
```

### 2. Add tuning strip to `UnifiedBrowserCore._build_window()` (after line 319, before treeview)

Insert between button-pack loop and `# Treeview` comment. Includes `<FocusOut>`/`<Return>`
bindings so typed edits persist (arrow-click `command=` alone does not cover keyboard entry):

```python
        # Download tuning strip (FTP/HTTP; SMB overrides _build_window entirely)
        tuning_frame = tk.Frame(self.window)
        tuning_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        tk.Label(tuning_frame, text="Workers").pack(side=tk.LEFT, padx=(0, 4))
        self.workers_var = tk.IntVar(value=self.download_workers)
        workers_spin = tk.Spinbox(
            tuning_frame, from_=1, to=3, width=3, textvariable=self.workers_var,
            command=self._persist_tuning,
        )
        workers_spin.pack(side=tk.LEFT)
        workers_spin.bind("<FocusOut>", lambda _e: self._persist_tuning())
        workers_spin.bind("<Return>",   lambda _e: self._persist_tuning())

        tk.Label(tuning_frame, text="Large files limit (MB)").pack(side=tk.LEFT, padx=(10, 4))
        self.large_mb_var = tk.IntVar(value=self.download_large_mb)
        _large_enabled = self._adapt_large_file_tuning_enabled()
        large_spin = tk.Spinbox(
            tuning_frame, from_=1, to=1024, width=5, textvariable=self.large_mb_var,
            command=self._persist_tuning,
            state=tk.NORMAL if _large_enabled else tk.DISABLED,
        )
        large_spin.pack(side=tk.LEFT)
        large_spin.bind("<FocusOut>", lambda _e: self._persist_tuning())
        large_spin.bind("<Return>",   lambda _e: self._persist_tuning())

        if not _large_enabled:
            # No fg= override; apply_theme_to_application() handles consistent styling
            tk.Label(
                tuning_frame,
                text="(HTTP large-file split not active in this version)",
            ).pack(side=tk.LEFT, padx=(6, 0))
```

Note: `<FocusOut>`/`<Return>` bindings on a `state=DISABLED` spinbox are inert — no double-fire risk.

### 3. Add tuning init to `FtpBrowserWindow.__init__` (before `self._build_window()`, ~line 573)

```python
        # Download tuning (UI only in C1; C2 wires runtime behavior)
        self.download_workers = 2
        self.download_large_mb = 25
        if self.settings_manager:
            try:
                self.download_workers = max(1, min(3, int(self.settings_manager.get_setting(
                    "file_browser.download_worker_count", self.download_workers
                ))))
                self.download_large_mb = max(1, int(self.settings_manager.get_setting(
                    "file_browser.download_large_file_mb", self.download_large_mb
                )))
            except Exception:
                pass
```

### 4. Add same tuning init to `HttpBrowserWindow.__init__` (before `self._build_window()`, ~line 1084)

Identical block to step 3.

### 5. Override `_adapt_large_file_tuning_enabled()` in `HttpBrowserWindow`

Add in the adapter-hooks section of `HttpBrowserWindow`:
```python
    def _adapt_large_file_tuning_enabled(self) -> bool:
        return False
```

### 6. Add tests to `test_ftp_browser_window.py` (6 new tests)

Add shared helpers at module level:

```python
class _IntVar:
    """Minimal IntVar stub — .get() returns a fixed int."""
    def __init__(self, value): self._value = value
    def get(self): return self._value

class _NoopThread:
    """Prevents real thread creation in init-load tests."""
    def __init__(self, *args, **kwargs): pass
    def start(self): pass
```

**Patch pattern for init-load tests** — use fresh `patch.multiple` inside each test to avoid
cross-test mock contamination. Also patch `threading.Thread` to prevent real thread objects:

```python
def test_init_loads_worker_count_from_settings_manager():
    sm = MagicMock()
    sm.get_setting.side_effect = lambda k, d: {"file_browser.download_worker_count": 3}.get(k, d)
    with patch.multiple(
        "gui.components.unified_browser_window.FtpBrowserWindow",
        _build_window=MagicMock(),
        _navigate_to=MagicMock(),
        _run_probe_background=MagicMock(),
        _apply_probe_snapshot=MagicMock(),
    ), patch("gui.components.unified_browser_window.threading.Thread", _NoopThread):
        win = FtpBrowserWindow(parent=MagicMock(), ip_address="1.2.3.4", settings_manager=sm)
    assert win.download_workers == 3
```

Six new tests:
1. `test_adapt_large_file_tuning_enabled_is_true_for_ftp` — `__new__` stub; asserts `_adapt_large_file_tuning_enabled()` returns `True`
2. `test_init_loads_worker_count_from_settings_manager` — sm returns 3; assert `win.download_workers == 3`
2. `test_init_clamps_worker_count_to_max_3` — sm returns 99; assert `win.download_workers == 3`
3. `test_init_clamps_worker_count_to_min_1` — sm returns 0; assert `win.download_workers == 1`
4. `test_init_loads_large_file_mb_from_settings_manager` — sm returns 50; assert `win.download_large_mb == 50`
6. `test_init_clamps_large_file_mb_to_min_1` — sm returns 0; assert `win.download_large_mb == 1`
7. `test_persist_tuning_writes_correct_settings_keys` — `__new__` stub + mock sm + `_IntVar` vars;
   calls `_persist_tuning()`; asserts `set_setting` called with `"file_browser.download_worker_count"` and `"file_browser.download_large_file_mb"`

### 7. Add tests to `test_http_browser_window.py` (8 new tests)

Same `_IntVar` / `_NoopThread` helpers at module level.

HTTP `__init__` instantiates `HttpNavigator` before `_build_window()`. Add
`patch("shared.http_browser.HttpNavigator")` in every init-load test alongside `patch.multiple`.

Tests 1–7 mirror the FTP set (swap class and add `_adapt_large_file_tuning_enabled` check):
1. `test_adapt_large_file_tuning_enabled_is_false_for_http` — `__new__` stub; asserts returns `False`
2. `test_init_loads_worker_count_from_settings_manager`
3. `test_init_clamps_worker_count_to_max_3`
4. `test_init_clamps_worker_count_to_min_1`
5. `test_init_loads_large_file_mb_from_settings_manager`
6. `test_init_clamps_large_file_mb_to_min_1`
7. `test_persist_tuning_writes_correct_settings_keys`

**Test 8 — UI-construction acceptance test** (verifies acceptance criteria without a real display):

```python
def test_build_window_renders_large_spinbox_disabled_with_note():
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
    win.download_workers = 2
    win.download_large_mb = 25
    win.settings_manager = None
    win.theme = None
    win.parent = MagicMock()
    win._server_banner = ""
    win._cancel_event = threading.Event()

    captured_spinboxes = []
    captured_label_texts = []

    def _fake_spinbox(*_args, **kwargs):
        captured_spinboxes.append(kwargs)
        return MagicMock()

    def _fake_label(*_args, **kwargs):
        captured_label_texts.append(kwargs.get("text", ""))
        return MagicMock()

    with patch("gui.components.unified_browser_window.tk.Toplevel", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Frame", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Text", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Button", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.StringVar", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.IntVar", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.ttk.Scrollbar", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.ttk.Treeview", return_value=MagicMock()), \
         patch("gui.components.unified_browser_window.tk.Spinbox", side_effect=_fake_spinbox), \
         patch("gui.components.unified_browser_window.tk.Label", side_effect=_fake_label):
        win._build_window()

    # Large spinbox: from_=1, to=1024 — must be DISABLED
    large_spins = [c for c in captured_spinboxes if c.get("to") == 1024]
    assert len(large_spins) == 1
    assert large_spins[0].get("state") == "disabled"

    # Explanatory note label must be present
    assert any("not active" in t for t in captured_label_texts)
```

`tk.DISABLED` equals the string `"disabled"` (a tkinter module constant), so the assertion
is valid even when `tk` is partially patched.

## Verification

```bash
python3 -m py_compile gui/components/unified_browser_window.py
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_browser_clamav.py -q
```

Expected: all existing tests pass + 15 new tests pass (7 FTP, 8 HTTP).

## Risks / Assumptions

- SMB behavior is fully isolated — SMB's `_build_window()` override means the base-class tuning strip never runs for SMB.
- `_persist_tuning()` on `UnifiedBrowserCore` mirrors the SMB version verbatim; no logic divergence.
- `test_browser_clamav.py` stubs (`_make_ftp_window`, `_make_http_window`) bypass `__init__` — they don't set `download_workers`/`download_large_mb`. These attrs are only needed at `_build_window()` time (not called in tests) and not yet used in `_download_thread_fn` (C2 scope). Existing tests unaffected.
- `<FocusOut>`/`<Return>` bindings on a `state=DISABLED` spinbox are inert — no double-fire risk.
- Theme styling via `apply_theme_to_application` is the project-standard approach; no per-widget color overrides needed for the note label.
- UI-construction test patches `tk.Spinbox` by identity (to=1024 distinguishes large from workers spinbox). If spinbox range changes in future, update the assertion.
