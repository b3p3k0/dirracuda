# C5 — Extract SMB Browser + Factory Layer

## Context

After C4, `SmbBrowserWindow`, `_load_smb_browser_config`, `_extract_smb_banner`, `open_smb_browser`, `open_ftp_http_browser`, and `_normalize_share_name` all remain in `gui/components/unified_browser_window.py`. C5 completes the modularization boundary by extracting these into two new modules under `gui/browsers/`. All existing import paths must continue to work via re-exports in the compatibility module.

---

## Files

| Action | Path |
|--------|------|
| NEW    | `gui/browsers/smb_browser.py` |
| NEW    | `gui/browsers/factory.py` |
| MOD    | `gui/components/unified_browser_window.py` |
| MOD    | `gui/browsers/__init__.py` |

---

## Step 1 — Create `gui/browsers/smb_browser.py`

Move from UBW (lines 81–1511):
- `_load_smb_browser_config` (lines 81–112)
- `_extract_smb_banner` (lines 115–150)
- `SmbBrowserWindow` class (lines 170–1511)

**Module-level imports to include:**
```python
from __future__ import annotations
import json, queue, threading, time, tkinter as tk
from tkinter import ttk
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from gui.utils import safe_messagebox as messagebox
from gui.utils.coercion import _coerce_bool
from gui.utils.filesize import _format_file_size
from gui.browsers.core import UnifiedBrowserCore

try:
    from gui.utils.dialog_helpers import ensure_dialog_focus
except ImportError:
    from utils.dialog_helpers import ensure_dialog_focus  # type: ignore[no-redef]

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
```

**Critical: lazy-import viewer pattern** — `_open_viewer` and `_open_image_viewer` in `SmbBrowserWindow` must import `open_file_viewer`/`open_image_viewer` from UBW at call-time, not at module level, to preserve monkeypatch targets:

```python
def _open_viewer(self, remote_path, content, size, truncated):
    from gui.components.unified_browser_window import open_file_viewer
    # ... call open_file_viewer(...)

def _open_image_viewer(self, remote_path, content, size, truncated, max_image_pixels):
    from gui.components.unified_browser_window import open_image_viewer
    # ... call open_image_viewer(...)
```

`_on_smb_download_done` — inherited from `UnifiedBrowserCore` (`core.py:373`). No action needed.

---

## Step 2 — Create `gui/browsers/factory.py`

Move from UBW (lines 1514–1619):
- `_normalize_share_name`
- `open_ftp_http_browser`
- `open_smb_browser`

Use **lazy imports inside each function body**. Critically, `open_ftp_http_browser` must import `FtpBrowserWindow` and `HttpBrowserWindow` from `gui.components.unified_browser_window` (not `gui.browsers.*`) so that `patch("gui.components.unified_browser_window.FtpBrowserWindow", ...)` in `test_action_routing.py` resolves correctly:

```python
def open_ftp_http_browser(host_type, parent, ip_address, port, *, ...):
    # Import from ubw, not gui.browsers.*, so monkeypatches on ubw are observed
    from gui.components.unified_browser_window import FtpBrowserWindow, HttpBrowserWindow
    ...

def open_smb_browser(parent, ip_address, shares, auth_method="", *, ...):
    from gui.browsers.smb_browser import SmbBrowserWindow, _extract_smb_banner
    ...
```

No module-level imports from `gui.browsers.*` or UBW needed in this file.

---

## Step 3 — Modify `gui/components/unified_browser_window.py`

**Remove** all implementations:
- `_load_smb_browser_config` function
- `_extract_smb_banner` function
- `IMAGE_EXTS` constant (used only by SmbBrowserWindow; now defined in smb_browser.py)
- `SmbBrowserWindow` class (full body)
- `open_ftp_http_browser` function definition
- `_normalize_share_name` function definition
- `open_smb_browser` function definition

**Add** re-export block (after the existing FTP/HTTP re-exports):
```python
from gui.browsers.smb_browser import (
    SmbBrowserWindow,
    _load_smb_browser_config,
    _extract_smb_banner,
)
from gui.browsers.factory import (
    open_ftp_http_browser,
    open_smb_browser,
    _normalize_share_name,
)
```

**Keep as-is** (these are required by compatibility contracts):
- `import threading`, `import queue`, `import tkinter as tk`, `from tkinter import ttk`, `from gui.utils import safe_messagebox as messagebox` (monkeypatch §2c/§2d targets)
- `open_file_viewer` and `open_image_viewer` wrapper functions (monkeypatch targets for viewer tests)
- `from gui.utils.coercion import _coerce_bool`
- `from gui.utils.filesize import _format_file_size`
- `from gui.browsers.core import UnifiedBrowserCore`
- `from gui.browsers.ftp_browser import FtpBrowserWindow, _load_ftp_browser_config`
- `from gui.browsers.http_browser import HttpBrowserWindow, _load_http_browser_config`

---

## Step 4 — Modify `gui/browsers/__init__.py`

After C5 there is no longer a circular for SMB/factory symbols. The `_LAZY_SYMBOLS` set shrinks to just the two UBW-defined wrappers that remain circular-sensitive (`open_file_viewer`, `open_image_viewer`). Direct imports replace all extracted symbols.

```python
from gui.browsers.core import UnifiedBrowserCore
from gui.browsers.ftp_browser import FtpBrowserWindow
from gui.browsers.http_browser import HttpBrowserWindow
from gui.browsers.smb_browser import SmbBrowserWindow, _extract_smb_banner
from gui.browsers.factory import open_ftp_http_browser, open_smb_browser
from gui.utils.coercion import _coerce_bool
from gui.utils.filesize import _format_file_size

# open_file_viewer / open_image_viewer still live in UBW; lazy-load to avoid
# the circular that arises when UBW imports gui.browsers.* during __init__.py init.
_LAZY_SYMBOLS = frozenset({"open_file_viewer", "open_image_viewer"})

def __getattr__(name: str):
    if name in _LAZY_SYMBOLS:
        from gui.components.unified_browser_window import (
            open_file_viewer, open_image_viewer,
        )
        _loaded = {"open_file_viewer": open_file_viewer, "open_image_viewer": open_image_viewer}
        globals().update(_loaded)
        return _loaded[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "UnifiedBrowserCore", "FtpBrowserWindow", "HttpBrowserWindow", "SmbBrowserWindow",
    "open_ftp_http_browser", "open_smb_browser",
    "open_file_viewer", "open_image_viewer",
    "_extract_smb_banner", "_coerce_bool", "_format_file_size",
]
```

**Why this is safe (corrected):** `from gui.browsers.smb_browser import ...` in UBW *does* trigger `gui/browsers/__init__.py` — Python always initialises the parent package first. The reason there is no circular is a different invariant: **`smb_browser.py` and `factory.py` must contain zero module-scope imports from `gui.components.unified_browser_window`**. Because they don't back-reference UBW at import time, `__init__.py` can import them during its own initialisation without looping back into a partially-initialised UBW. The import ordering inside `__init__.py` also matters — `core` must be imported before `smb_browser` so it is already in `sys.modules` when `smb_browser.py` does `from gui.browsers.core import UnifiedBrowserCore`.

The two remaining UBW-only symbols (`open_file_viewer`, `open_image_viewer`) are still lazy-loaded via `__getattr__` because they *are* defined in UBW and importing them at `__init__.py` module scope would be a direct back-reference.

---

## Identity contracts analysis

| Check | After C5 |
|-------|----------|
| `gui.browsers.SmbBrowserWindow is ubw.SmbBrowserWindow` | Both resolve to `gui.browsers.smb_browser.SmbBrowserWindow` ✓ |
| `gui.browsers.open_smb_browser is ubw.open_smb_browser` | Both resolve to `gui.browsers.factory.open_smb_browser` ✓ |
| `gui.browsers.open_ftp_http_browser is ubw.open_ftp_http_browser` | Both resolve to `gui.browsers.factory.open_ftp_http_browser` ✓ |
| `gui.browsers.open_file_viewer is ubw.open_file_viewer` | Both resolve to UBW's wrapper function ✓ |

---

## Validation (run in order)

```bash
# 0) Enforce the zero-module-scope-UBW-import invariant for new files
# Step 0a: show all UBW imports in the new files (indented = OK, column-0 = violation)
rg -n "^[[:space:]]*(from|import) gui\.components\.unified_browser_window" \
  gui/browsers/smb_browser.py gui/browsers/factory.py

# Step 0b: fail explicitly if any column-0 (module-scope) UBW import exists
! grep -Pn "^(from|import) gui\.components\.unified_browser_window" \
    gui/browsers/smb_browser.py gui/browsers/factory.py
# Exit non-zero (contract violation) if any column-0 match is found; exit 0 = clean.

# A) Compile
python3 -m py_compile \
  gui/browsers/smb_browser.py \
  gui/browsers/factory.py \
  gui/browsers/ftp_browser.py \
  gui/browsers/http_browser.py \
  gui/browsers/core.py \
  gui/browsers/__init__.py \
  gui/components/unified_browser_window.py

# B) C5 targeted (SMB/factory focus + FTP/HTTP regression for changed wiring)
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_smb_virtual_root.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_action_routing.py \
  gui/tests/test_browser_clamav.py \
  gui/tests/test_c2_browser_import_contracts.py \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  -q

# C) Import smoke + identity checks
./venv/bin/python -c "
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser, open_file_viewer, open_image_viewer,
    UnifiedBrowserCore, FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
    _load_ftp_browser_config, _load_http_browser_config, _load_smb_browser_config,
    _normalize_share_name,
)
import gui.components.unified_browser_window as ubw
import gui.browsers
from gui.browsers.smb_browser import SmbBrowserWindow as SmbFromPkg
from gui.browsers.factory import open_smb_browser as OpenSmbFromPkg, open_ftp_http_browser as OpenFhFromPkg
assert SmbBrowserWindow is SmbFromPkg
assert open_smb_browser is OpenSmbFromPkg
assert open_ftp_http_browser is OpenFhFromPkg
assert hasattr(ubw, 'threading')
assert hasattr(ubw, 'messagebox')
assert hasattr(ubw, 'queue')
assert hasattr(ubw, 'tk')
assert hasattr(ubw, 'ttk')
print('IMPORT SMOKE: PASS')
"

# D) Full regression
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_c5.txt 2>&1; RESULT=$?
cat /tmp/pytest_c5.txt
echo "pytest exit=${RESULT}"
```

Expected: same 2 pre-existing DB failures only; any additional failure = C5 regression.
