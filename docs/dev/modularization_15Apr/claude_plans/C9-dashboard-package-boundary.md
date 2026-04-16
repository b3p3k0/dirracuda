# C9 — Dashboard Package Extraction + Compatibility Shim

## Context

C7 and C8 extracted `DashboardWidget`'s scan and batch-ops logic into sibling modules
(`dashboard_scan.py`, `dashboard_batch_ops.py`), each using `_mb()` / `_d()` helpers to
preserve frozen patch paths at `gui.components.dashboard.*`. C9 extends this by:

1. Creating a proper `gui/dashboard/` package for the widget class.
2. Converting `gui/components/dashboard.py` into a shim so all existing imports, tests, and
   frozen patch paths remain unbroken.

The invariant that makes this safe: tests patch names at `gui.components.dashboard.*`.
After C9 the shim still owns those names at module scope; `_mb()` / `_d()` helpers in
`widget.py` resolve them from `sys.modules['gui.components.dashboard']` at call time.

---

## Scope of change

**12 call-site edits** in the class body (identified by grep):

| Line | Current | Replacement |
|------|---------|-------------|
| 471 | `messagebox.showerror(…)` | `_mb().showerror(…)` |
| 840 | `messagebox.showwarning(…)` | `_mb().showwarning(…)` |
| 847 | `show_unified_scan_dialog(…)` | `_d('show_unified_scan_dialog')(…)` |
| 1041 | `messagebox.showerror(…)` | `_mb().showerror(…)` |
| 1204 | `messagebox.showerror(…)` | `_mb().showerror(…)` |
| 1347 | `messagebox.showinfo(…)` | `_mb().showinfo(…)` |
| 1394 | `show_reddit_grab_dialog(…)` | `_d('show_reddit_grab_dialog')(…)` |
| 1412 | `threading.Thread(…)` | `_d('threading').Thread(…)` |
| 1421 | `run_ingest(options)` | `_d('run_ingest')(options)` |
| 1453 | `messagebox.showerror(…)` | `_mb().showerror(…)` |
| 1474 | `messagebox.showinfo(…)` | `_mb().showinfo(…)` |
| 1483 | `messagebox.showinfo(…)` | `_mb().showinfo(…)` |

`tk.*` / `ttk.*` widget construction inside the class body is NOT patched by any test
(the `test_extract_runner_clamav` tk patches are intercepted by `dashboard_batch_ops._d()`
because `_execute_batch_extract` delegates to that module). No `_d('tk')` changes needed.

---

## Files to create / modify

### NEW: `gui/dashboard/__init__.py`

```python
# Load the shim first so _mb()/_d() in widget.py, dashboard_scan.py,
# and dashboard_batch_ops.py can resolve gui.components.dashboard via sys.modules.
# Idempotent: no-op if the shim is already in sys.modules.
import gui.components.dashboard  # noqa: F401
from gui.dashboard.widget import DashboardWidget

__all__ = ["DashboardWidget"]
```

**Why this order:** `gui/dashboard/__init__.py` imports the shim, which in turn does
`from gui.dashboard.widget import DashboardWidget` (loading widget.py). By the time
`__init__.py`'s own `from gui.dashboard.widget import …` runs, widget.py is already
cached. No circular import — the shim reaches `gui.dashboard.widget` directly (a submodule
import), not through `gui.dashboard.__init__`.

**Self-sufficiency guarantee:** any consumer who does only
`from gui.dashboard import DashboardWidget` gets the shim loaded as a side-effect, so all
`_d()` / `_mb()` calls in widget.py, dashboard_scan.py, and dashboard_batch_ops.py succeed.

### NEW: `gui/dashboard/widget.py`

Structure:
1. **All imports** — identical block to current `dashboard.py` top (lines 12–56), plus:
   ```python
   from gui.utils import safe_messagebox as _fallback_msgbox
   ```
2. **`_mb()` helper** — copy verbatim from `dashboard_batch_ops.py`:
   ```python
   def _mb():
       mod = sys.modules.get("gui.components.dashboard")
       if mod is not None and hasattr(mod, "messagebox"):
           return mod.messagebox
       return _fallback_msgbox
   ```
3. **`_d()` helper** — copy verbatim from `dashboard_batch_ops.py`:
   ```python
   def _d(name: str):
       mod = sys.modules.get("gui.components.dashboard")
       if mod is not None:
           return getattr(mod, name)
       raise RuntimeError(f"gui.components.dashboard not yet loaded (looking for {name!r})")
   ```
4. **`DashboardWidget` class** — full body from `dashboard.py` (lines 61–1751) with the 12
   call-site substitutions in the table above. No other changes.

### MOD: `gui/components/dashboard.py`

- Keep every import line exactly as-is (lines 12–56 + `_logger`).  
  These 14 names at module scope are the frozen patch targets the tests rely on:
  `messagebox`, `threading`, `tk`, `ttk`, `show_unified_scan_dialog`,
  `show_reddit_grab_dialog`, `run_ingest`, `dispatch_probe_run`, `probe_patterns`,
  `get_probe_snapshot_path_for_host`, `create_quarantine_dir`, `extract_runner`,
  `dashboard_logs`, `dashboard_status`, `dashboard_scan`, `dashboard_batch_ops`.
- **Remove** the `DashboardWidget` class body (lines 61–1751).
- **Append** at end:
  ```python
  from gui.dashboard.widget import DashboardWidget   # noqa: E402
  ```

No other files need modification (dashboard_scan.py, dashboard_batch_ops.py, etc. are
unchanged).

---

## Why this works

| Requirement | How satisfied |
|---|---|
| `from gui.components.dashboard import DashboardWidget` | shim re-exports it |
| `from gui.dashboard import DashboardWidget` | `__init__.py` exports it |
| Identity: `OldDash is NewDash` | both resolve to the same class object from `widget.py` |
| Frozen patch paths exist at `gui.components.dashboard.*` | original imports stay in shim |
| Patches intercepted at call time | `_mb()` / `_d()` look up `sys.modules['gui.components.dashboard']` |
| `DashboardWidget._extract_single_server` patchable | class is a regular class, method is patchable |
| `gui.dashboard` is self-sufficient | `__init__.py` ensures shim is in `sys.modules` before any `_d()` call happens |
| No circular import | `widget.py` never imports `gui.components.dashboard`; shim reaches `widget.py` via direct submodule import, bypassing `__init__.py` |

---

## Verification (run in order)

**A) Compile**
```bash
python3 -m py_compile \
  gui/components/dashboard.py \
  gui/components/dashboard_scan.py \
  gui/components/dashboard_batch_ops.py \
  gui/dashboard/__init__.py \
  gui/dashboard/widget.py
```

**B) Dashboard contract tests**
```bash
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_extract_runner_clamav.py \
  gui/tests/test_clamav_results_dialog.py \
  gui/tests/test_theme_runtime_toggle.py \
  gui/tests/test_ftp_scan_dialog.py \
  -q
```

**C) Import + identity smoke**
```bash
./venv/bin/python -c "
from gui.components.dashboard import DashboardWidget as OldDash
from gui.dashboard import DashboardWidget as NewDash
import gui.components.dashboard as dmod
assert OldDash is NewDash
for name in [
    'messagebox','threading','tk','ttk',
    'show_unified_scan_dialog','show_reddit_grab_dialog','run_ingest',
    'dispatch_probe_run','probe_patterns','get_probe_snapshot_path_for_host',
    'create_quarantine_dir','extract_runner'
]:
    assert hasattr(dmod, name), f'missing dashboard shim name: {name}'
print('IMPORT SMOKE: PASS')
"
```

**D) Full regression**
```bash
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_c9.txt 2>&1; RESULT=$?
cat /tmp/pytest_c9.txt
echo "pytest exit=${RESULT}"
```
Expected: only the 2 known pre-existing DB failures.

**E) Manual HI gate**
- Full app startup via `./dirracuda`
- Run scan, view results, open browser, exercise dashboard controls
- Start with pre-migration DB, confirm startup succeeds
