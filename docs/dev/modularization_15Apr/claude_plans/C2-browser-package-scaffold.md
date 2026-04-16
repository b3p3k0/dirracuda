# Plan: Card C2 — Browser Package Scaffold + Compatibility Exports

## Context

C1 is complete: `gui/utils/coercion.py` and `gui/utils/filesize.py` exist; `unified_browser_window.py` already imports `_coerce_bool` and `_format_file_size` from those modules.

C2's goal is **pure scaffolding** — create `gui/browsers/` as an importable package that re-exports the frozen public symbols from `gui.components.unified_browser_window`, with no code movement and no behavior change. Future cards (C3–C5) will progressively extract implementations into this package.

Pre-existing state: 2 known failures in `test_database_access_protocol_writes.py` (schema gap, unrelated). Gate: exactly those 2 failures must remain — any additional failure is a C2 regression. Do not hardcode pass counts; they drift across cards.

---

## Files Changed

| File | Action |
|---|---|
| `gui/browsers/__init__.py` | **Create** — package scaffold with re-exports |
| `gui/tests/test_c2_browser_import_contracts.py` | **Create** — import-contract test coverage |
| `gui/components/unified_browser_window.py` | **No change** — already the compatibility entrypoint |

---

## Implementation

### 1. `gui/browsers/__init__.py`

Re-export all frozen public symbols from `gui.components.unified_browser_window`. No imports at module scope beyond the re-exports (this avoids duplicating the tk/threading etc. imports that must remain on `unified_browser_window` for monkeypatch contracts in §2c/§2d).

```python
"""
gui/browsers — Browser package scaffold (Card C2).

This package provides the future home of browser implementations.
Phase C2: package skeleton only — all implementations remain in
gui.components.unified_browser_window. Later cards (C3–C5) progressively
extract into dedicated modules here.
"""
from gui.components.unified_browser_window import (
    UnifiedBrowserCore,
    FtpBrowserWindow,
    HttpBrowserWindow,
    SmbBrowserWindow,
    open_ftp_http_browser,
    open_smb_browser,
    open_file_viewer,
    open_image_viewer,
    _extract_smb_banner,
    _coerce_bool,
    _format_file_size,
)

__all__ = [
    "UnifiedBrowserCore",
    "FtpBrowserWindow",
    "HttpBrowserWindow",
    "SmbBrowserWindow",
    "open_ftp_http_browser",
    "open_smb_browser",
    "open_file_viewer",
    "open_image_viewer",
    "_extract_smb_banner",
    "_coerce_bool",
    "_format_file_size",
]
```

### 2. `gui/tests/test_c2_browser_import_contracts.py`

Tests that:
- All frozen public symbols still import from legacy canonical path (`gui.components.unified_browser_window`)
- `ubw.*` module-attribute monkeypatch contracts (`threading`, `messagebox`, `queue`, `tk`, `ttk`) remain intact
- `gui.browsers` package is importable
- `gui.browsers` re-exports all frozen symbols as the same objects (not copies)

---

## Validation Commands (in order)

```bash
# A) Compile
python3 -m py_compile gui/browsers/__init__.py gui/components/unified_browser_window.py

# B) C2 targeted (includes the new scaffold test)
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_action_routing.py gui/tests/test_c2_browser_import_contracts.py -q

# C) Import smoke (includes gui.browsers check)
./venv/bin/python -c "
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
import gui.browsers
print('IMPORT SMOKE: PASS')
"

# D) Full regression (must show exactly 2 pre-existing failures, no new ones)
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_c2.txt 2>&1; RESULT=$?
cat /tmp/pytest_c2.txt
echo "pytest exit=${RESULT}"
```

---

## Risks

- **Transitive import surface (low):** `gui/browsers/__init__.py` imports from `unified_browser_window`, which in turn pulls in `tkinter`, `threading`, `queue`, `gui.utils.safe_messagebox`, etc. at module scope. If any of those imports fail in a headless/test context, `import gui.browsers` will fail too. Mitigated by the existing test suite already exercising `unified_browser_window` imports successfully.
- `_coerce_bool` and `_format_file_size` resolve correctly — after C1 they are bound at module scope in `unified_browser_window` via `from gui.utils.coercion import _coerce_bool` / `from gui.utils.filesize import _format_file_size`.
- No monkeypatch paths are affected — all `ubw.*` contract targets remain on `gui.components.unified_browser_window`, not `gui.browsers`.
