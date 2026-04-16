# Plan: C1 QA Follow-up — Dashboard Coercion Regression Lock (real method)

## Context

The `TestDashboardCoerceBoolPreservedSemantics` class in `gui/tests/test_c1_coercion_filesize.py` currently asserts against a local `_dashboard_coerce_bool` helper that is a copied body of `DashboardWidget._coerce_bool`. A local copy defeats the purpose of the regression lock — if the production method were inadvertently changed, the copy would still pass. The fix imports and calls the real static method directly.

## Scope

Single file edit: `gui/tests/test_c1_coercion_filesize.py`

1. Add `from gui.components.dashboard import DashboardWidget` to imports (alongside existing `_coerce_bool`, `_format_file_size` imports).
2. Remove the `_dashboard_coerce_bool` `@staticmethod` body (lines 158–166).
3. Replace all 10 `self._dashboard_coerce_bool(...)` calls with `DashboardWidget._coerce_bool(...)`.
4. Update the class docstring to reflect that the real method is now used.

No production code changes. No behavior changes.

## Validation

```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_c1_coercion_filesize.py -q
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q
# Expected: c1 file passes; full suite 1037 passed / 2 pre-existing failures
```

## HI Test Needed?
No.

---

# Previous Plan: C1 — Shared Utility Consolidation (Coercion + File Size)

## Context

`gui/components/unified_browser_window.py` and `gui/components/file_viewer_window.py` each define their own `_format_file_size` — the implementations are byte-for-byte identical. `_coerce_bool` is defined at module-level in `unified_browser_window.py` (10+ call sites) and as a `@staticmethod` on `DashboardWidget` in `dashboard.py` (1 call site, slightly simpler variant). C1 centralizes both helpers into `gui/utils/`, replaces the duplicate bodies with imports, and adds targeted unit tests. No behavior changes are in scope.

---

## Canonical Implementations

### `_coerce_bool` (from `unified_browser_window.py:93–104` — canonical for UBW/filesize)

```python
def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default
```

**Dashboard semantics are different and must be preserved.** `DashboardWidget._coerce_bool` uses a str-only path — `int(2)` → `False` (not in truthy-string set), whereas the UBW utility returns `bool(2)` → `True`. These are not identical duplicates; they are related-but-distinct variants. Dashboard's `@staticmethod _coerce_bool` is **kept in place with its original body** (no behavior change). The card touch to `dashboard.py` is:
1. Import `_coerce_bool` from `gui.utils.coercion` at module scope (makes the utility available for future use within the module)
2. Keep the `@staticmethod _coerce_bool` method body unchanged — it remains the authoritative implementation for dashboard config coercion
3. The call site at line 711 (`self._coerce_bool(...)`) is **not changed**

### `_format_file_size` (identical in both files)

```python
def _format_file_size(size_bytes: int) -> str:
    """Convert bytes to human-readable format (e.g., '1.6 MB')."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[unit_index]}"
```

---

## Files Changed

### New: `gui/utils/coercion.py`
- Module-level `_coerce_bool(value, default=False)` — exact UBW body
- `from typing import Any`

### New: `gui/utils/filesize.py`
- Module-level `_format_file_size(size_bytes)` — exact shared body

### Modified: `gui/components/unified_browser_window.py`
- **Add** at top (after existing imports, before `open_file_viewer`):
  ```python
  from gui.utils.coercion import _coerce_bool
  from gui.utils.filesize import _format_file_size
  ```
- **Remove** local function bodies at lines 78–104 (the `_format_file_size` and `_coerce_bool` definitions)
- Names remain importable from this module via the import above — satisfies the compatibility constraint

### Modified: `gui/components/file_viewer_window.py`
- **Add** after existing try/except import block:
  ```python
  try:
      from gui.utils.filesize import _format_file_size
  except ImportError:
      from utils.filesize import _format_file_size  # type: ignore[no-redef]
  ```
- **Remove** local function body at lines 54–66

### Modified: `gui/components/dashboard.py`
- **No import added** — the shared utility is not consumed in dashboard.py within C1 scope; importing it unused would be avoidable churn.
- **Keep** `@staticmethod _coerce_bool` at lines 687–694 **unchanged** — semantics differ from the shared utility; removing or replacing would change `int(2)` from `False` to `True` for dashboard config coercion. This is an **explicit no-behavior-change decision**.
- **No change** to call site at line 711 (`self._coerce_bool(...)` stays)
- **Net delta**: zero lines changed in dashboard.py; the file appears in the "touch targets" list because it was audited and a conscious no-change decision was made.

### New: `gui/tests/test_c1_coercion_filesize.py`
- `TestCoerceBool` (~15 cases): bool passthrough, int/float truthiness (`int(0)` → False, `int(1)` → True, `int(2)` → True, `float(0.0)` → False, `float(1.5)` → True), str variants (case-insensitive, with whitespace), None → default, unrecognized str → default, explicit default=True
- `TestFormatFileSize`: 0 B, sub-KB, exact 1 KB boundary, 1.5 KB, 1.6 MB, 1.0 GB, 1.0 TB
- `TestDashboardCoerceBoolPreservedSemantics`: explicit regression for `int(2)` → `False` using the dashboard static method directly — **locks in pre-existing behavior so any future drift fails loudly**

---

## Compatibility Constraints Preserved

- `_coerce_bool` and `_format_file_size` remain importable from `gui.components.unified_browser_window` (imported via the new module-level `from gui.utils.X import Y`)
- `ubw.threading`, `ubw.messagebox`, `ubw.queue`, `ubw.tk`, `ubw.ttk` — untouched
- All frozen public symbols unchanged
- No schema/migration/DB changes
- No parser/output contract changes

---

## Validation

```bash
# A) Compile check
python3 -m py_compile gui/utils/coercion.py gui/utils/filesize.py \
  gui/components/dashboard.py gui/components/unified_browser_window.py \
  gui/components/file_viewer_window.py

# B) C1-targeted test suite (includes new test file for fast failure isolation)
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_c1_coercion_filesize.py \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_virtual_root.py \
  -q

# C) Import smoke
./venv/bin/python -c "
from gui.components.dashboard import DashboardWidget
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser, open_file_viewer, open_image_viewer,
    UnifiedBrowserCore, FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
)
import gui.components.unified_browser_window as ubw
assert hasattr(ubw, 'threading')
assert hasattr(ubw, 'messagebox')
assert hasattr(ubw, 'queue')
assert hasattr(ubw, 'tk')
assert hasattr(ubw, 'ttk')
print('IMPORT SMOKE: PASS')
"

# D) Full baseline regression
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q
# Expected: exactly the same 2 pre-existing failures:
#   test_manual_upsert_inserts_smb_ftp_http_rows
#   test_manual_upsert_http_same_ip_different_ports_create_distinct_rows
# Pass count will be higher than 994 due to new test file; that is expected.
# Any failure outside those two is a C1 regression.
```

---

## HI Test Needed?
No — C1 is utility-only; no runtime GUI behavior changes.
