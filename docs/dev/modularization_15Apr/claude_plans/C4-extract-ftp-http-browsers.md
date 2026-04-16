# C4 — Extract FTP + HTTP Browser Modules

## Context

Cards C1–C3 are complete. `UnifiedBrowserCore` now lives in `gui/browsers/core.py`.
`FtpBrowserWindow` and `HttpBrowserWindow` still live in the 2797-line monolith
`gui/components/unified_browser_window.py`. C4 physically moves them into dedicated
modules while keeping `unified_browser_window.py` as a compatibility re-export shim
and preserving every frozen monkeypatch path from BASELINE_CONTRACTS.md §2c/§2d.

---

## Critical constraint: monkeypatch paths

Patches on **module singletons** (`threading`, `messagebox`, `queue`, `tk`, `ttk`)
work anywhere — patching `ubw.threading.Thread` patches the global `threading` module,
which affects all code in any file. No special handling needed.

Patches on **function objects** in ubw's namespace (`open_file_viewer`,
`open_image_viewer`) are different. They replace the *name* in ubw's `__dict__`.
If `FtpBrowserWindow._open_viewer()` resolves `open_file_viewer` from `ftp_browser.py`'s
namespace instead of ubw's, the patch is invisible to it.

**Fix:** In `ftp_browser.py` and `http_browser.py`, the `_open_viewer` and
`_open_image_viewer` methods use a **lazy import at call time**:

```python
def _open_viewer(self, remote_path, content, file_size):
    from gui.components.unified_browser_window import open_file_viewer
    open_file_viewer(parent=self.window, ...)
```

`from module import name` at runtime fetches the **current** value from the module's
`__dict__`. If the test has already patched `ubw.open_file_viewer`, this returns the
mock. No circular import risk: by method-call time both modules are fully initialised.

**Circular-import enforcement rule:** `ftp_browser.py` and `http_browser.py` must
have **zero** `import gui.components.unified_browser_window` or
`from gui.components.unified_browser_window import ...` statements at module scope.
The only permitted references to `unified_browser_window` are the two lazy imports
inside `_open_viewer` and `_open_image_viewer`. Verify after writing each file with:
```bash
grep -n "unified_browser_window" gui/browsers/ftp_browser.py gui/browsers/http_browser.py
```
The only lines that should appear are inside the two method bodies.

---

## Import order when `unified_browser_window` is loaded

1. `unified_browser_window.py` starts
2. `from gui.browsers.core import UnifiedBrowserCore` → triggers `gui.browsers.__init__`
3. `__init__.py` imports `UnifiedBrowserCore`, then `FtpBrowserWindow` from
   `gui.browsers.ftp_browser`, then `HttpBrowserWindow` from `gui.browsers.http_browser`
4. Both submodules load cleanly (no dep on ubw at module level)
5. `__init__.py` finishes; back in `unified_browser_window.py`
6. Later: `from gui.browsers.ftp_browser import FtpBrowserWindow, _load_ftp_browser_config`
   → already in `sys.modules`, cheap lookup ✓
7. `unified_browser_window.py` finishes

---

## Files to create

### `gui/browsers/ftp_browser.py`

**Contents (in order):**
- Module docstring (C4 extraction note)
- `from __future__ import annotations`
- Standard imports: `json, queue, threading, time, tkinter as tk, from tkinter import ttk`
- `from datetime import datetime`, `from pathlib import Path, PurePosixPath`
- `from typing import Any, Dict, List, Optional`
- `from gui.utils import safe_messagebox as messagebox`
- `from gui.utils.coercion import _coerce_bool`
- `from gui.utils.filesize import _format_file_size`
- `from gui.browsers.core import UnifiedBrowserCore`
- `IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}`
- `def _load_ftp_browser_config(config_path)` — verbatim copy from ubw.py lines 87–111
- `class FtpBrowserWindow(UnifiedBrowserCore)` — verbatim copy from ubw.py lines 215–799

**Modifications inside FtpBrowserWindow (only these two methods change):**

`_open_viewer`: replace module-level `open_file_viewer(...)` with:
```python
def _open_viewer(self, remote_path, content, file_size):
    from gui.components.unified_browser_window import open_file_viewer
    display_path = f"{self.ip_address}/ftp_root{remote_path}"
    def save_callback():
        self._start_download_thread([(remote_path, file_size)])
    open_file_viewer(parent=self.window, file_path=display_path, content=content,
                     file_size=file_size, theme=self.theme, on_save_callback=save_callback)
    self._set_status(f"Viewing {remote_path}")
```

`_open_image_viewer`: replace `open_image_viewer(...)` with:
```python
def _open_image_viewer(self, remote_path, content, file_size, truncated, max_image_pixels):
    from gui.components.unified_browser_window import open_image_viewer
    ...
    open_image_viewer(parent=self.window, ...)
```

### `gui/browsers/http_browser.py`

Same pattern as `ftp_browser.py`. Explicit checklist:
- Module-level imports: same set (json, queue, threading, time, tk, ttk, datetime,
  Path, PurePosixPath, typing, messagebox, _coerce_bool, _format_file_size, UnifiedBrowserCore)
- `IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}`
  — **must be defined here too**: `HttpBrowserWindow._on_view()` references it (line 1031 in
  original ubw.py)
- `def _load_http_browser_config(config_path)` — verbatim copy from ubw.py lines 114–136
- `class HttpBrowserWindow(UnifiedBrowserCore)` — verbatim copy from ubw.py lines 804–1342
- `_open_viewer` and `_open_image_viewer` use the same lazy-import pattern

---

## Files to modify

### `gui/components/unified_browser_window.py`

1. **Remove** the shared-helpers comment block (lines 81–84) that says "FTP config
   loader, HTTP config loader" — those loaders are moving.
2. **Remove** `_load_ftp_browser_config` function body (lines 87–111).
3. **Remove** `_load_http_browser_config` function body (lines 114–136).
4. **Replace** FtpBrowserWindow section comment + class (lines ~211–799) with:
   ```python
   # ---------------------------------------------------------------------------
   # FtpBrowserWindow — extracted to gui.browsers.ftp_browser (Card C4)
   # ---------------------------------------------------------------------------
   from gui.browsers.ftp_browser import FtpBrowserWindow, _load_ftp_browser_config
   ```
   Re-exporting `_load_ftp_browser_config` here is **required**: `test_browser_clamav.py`
   imports it directly from `gui.components.unified_browser_window`.

5. **Replace** HttpBrowserWindow section comment + class (lines ~800–1342) with:
   ```python
   # ---------------------------------------------------------------------------
   # HttpBrowserWindow — extracted to gui.browsers.http_browser (Card C4)
   # ---------------------------------------------------------------------------
   from gui.browsers.http_browser import HttpBrowserWindow, _load_http_browser_config
   ```
   Re-exporting `_load_http_browser_config` here is **required** for the same reason.
6. Keep unchanged: `IMAGE_EXTS` (used by SmbBrowserWindow at line 1814),
   `open_file_viewer`, `open_image_viewer`, `_load_smb_browser_config`,
   `_extract_smb_banner`, `SmbBrowserWindow`, `open_ftp_http_browser`,
   `_normalize_share_name`, `open_smb_browser`, all module-level imports
   (`threading`, `queue`, `tk`, `ttk`, `messagebox`, etc.).

### `gui/browsers/__init__.py`

Replace lazy loading of `FtpBrowserWindow` and `HttpBrowserWindow` with direct
module-level imports. Keep lazy loading for symbols still in `unified_browser_window`:
`SmbBrowserWindow`, `open_ftp_http_browser`, `open_smb_browser`, `open_file_viewer`,
`open_image_viewer`, `_extract_smb_banner`, `_coerce_bool`, `_format_file_size`.

```python
from gui.browsers.core import UnifiedBrowserCore
from gui.browsers.ftp_browser import FtpBrowserWindow
from gui.browsers.http_browser import HttpBrowserWindow

_LAZY_SYMBOLS = frozenset({
    "SmbBrowserWindow",
    "open_ftp_http_browser",
    "open_smb_browser",
    "open_file_viewer",
    "open_image_viewer",
    "_extract_smb_banner",
    "_coerce_bool",
    "_format_file_size",
})

def __getattr__(name):
    if name in _LAZY_SYMBOLS:
        from gui.components.unified_browser_window import (...)
        globals().update(_loaded)
        return _loaded[name]
    raise AttributeError(...)
```

---

## Compatibility guarantees (all must hold)

| Contract | How preserved |
|---|---|
| `from gui.components.unified_browser_window import FtpBrowserWindow` | Re-exported from ftp_browser.py |
| `from gui.components.unified_browser_window import HttpBrowserWindow` | Re-exported from http_browser.py |
| `from gui.components.unified_browser_window import _load_ftp_browser_config` | Re-exported from ftp_browser.py — required by test_browser_clamav.py |
| `from gui.components.unified_browser_window import _load_http_browser_config` | Re-exported from http_browser.py — required by test_browser_clamav.py |
| `from gui.browsers.ftp_browser import FtpBrowserWindow` | Defined there |
| `from gui.browsers.http_browser import HttpBrowserWindow` | Defined there |
| `assert FtpBrowserWindow is FtpFromPkg` | Same object — ubw re-exports the class, not a copy |
| `ubw.threading`, `ubw.messagebox`, `ubw.queue`, `ubw.tk`, `ubw.ttk` | Module-level imports kept in ubw |
| `ubw.open_file_viewer` patch affects `FtpBrowserWindow._open_viewer` | Lazy import inside method |
| `ubw.open_image_viewer` patch affects `*._open_image_viewer` | Lazy import inside method |
| `ubw.FtpBrowserWindow` patch affects `open_ftp_http_browser` | Name in ubw's namespace; function looks up at call time |
| `ubw.show_clamav_results_dialog` | Not a real module-level name; patches use `create=True` |

---

## Validation

```bash
# A) Compile
python3 -m py_compile gui/browsers/ftp_browser.py gui/browsers/http_browser.py \
  gui/browsers/core.py gui/browsers/__init__.py gui/components/unified_browser_window.py

# B) C4 targeted tests (includes __init__ contract check)
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py \
  gui/tests/test_action_routing.py gui/tests/test_browser_clamav.py \
  gui/tests/test_c2_browser_import_contracts.py -q

# C) Import smoke
./venv/bin/python -c "
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser, open_file_viewer, open_image_viewer,
    UnifiedBrowserCore, FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
)
import gui.components.unified_browser_window as ubw
import gui.browsers
from gui.browsers.ftp_browser import FtpBrowserWindow as FtpFromPkg
from gui.browsers.http_browser import HttpBrowserWindow as HttpFromPkg
assert FtpBrowserWindow is FtpFromPkg
assert HttpBrowserWindow is HttpFromPkg
assert hasattr(ubw, 'threading')
assert hasattr(ubw, 'messagebox')
assert hasattr(ubw, 'queue')
assert hasattr(ubw, 'tk')
assert hasattr(ubw, 'ttk')
print('IMPORT SMOKE: PASS')
"

# D) Full regression — expect exactly 2 pre-existing failures
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_full.txt 2>&1; RESULT=$?
cat /tmp/pytest_full.txt
echo "pytest exit=${RESULT}"
```

Expected: exactly `test_manual_upsert_inserts_smb_ftp_http_rows` and
`test_manual_upsert_http_same_ip_different_ports_create_distinct_rows` failing.
Any additional failure is a C4 regression.

---

## HI manual test (required for C4 gate)

1. Open one FTP host from Server List → verify file listing renders, Up button works, View opens file, Download to Quarantine creates file.
2. Open one HTTP host → same checks.
