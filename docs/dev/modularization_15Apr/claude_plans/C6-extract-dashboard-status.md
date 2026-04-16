# C6 — Extract Dashboard Runtime Status Logic

## Context

`gui/components/dashboard.py` contains three pure/near-pure helpers that compose ClamAV and tmpfs runtime status display lines. These helpers are self-contained enough to live in a dedicated module (`dashboard_status.py`), but they currently sit inside `DashboardWidget` — blocking modularization progress. C6 extracts them into a new file while keeping all public call signatures and patch-sensitive names intact.

No behavior change. No new tests required (existing test suite covers the extraction surface).

---

## Critical Files

- **MOD** `gui/components/dashboard.py` (lines 687–729)
- **NEW** `gui/components/dashboard_status.py`

---

## Step-by-Step Plan

### 1. Create `gui/components/dashboard_status.py`

Pure module — no Tkinter, no instance state, no side effects.

```python
"""Pure helpers for dashboard runtime status composition (C6 extraction)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def coerce_bool_dashboard(value: Any) -> bool:
    """DashboardWidget._coerce_bool semantics.

    Diverges from gui.utils.coercion._coerce_bool:
    - No int/float branch: int(2) → str("2") not in truthy set → False
    - No `default` parameter
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_clamav_backend(value: Any) -> str:
    """Normalize backend mode to one of auto/clamdscan/clamscan."""
    backend = str(value or "auto").strip().lower()
    return backend if backend in {"auto", "clamdscan", "clamscan"} else "auto"


def compose_runtime_status_lines(
    clamav_cfg: Dict[str, Any],
    tmpfs_state: Dict[str, Any],
) -> tuple[str, str]:
    """Build ClamAV/tmpfs status lines from pre-loaded config dicts.

    Args:
        clamav_cfg:   dict with keys 'enabled' and 'backend' (already resolved)
        tmpfs_state:  dict with keys 'tmpfs_active' and 'mountpoint' (already resolved)

    Returns:
        (clamav_line, tmpfs_line) — formatted status strings
    """
    clamav_enabled = coerce_bool_dashboard(clamav_cfg.get("enabled", False))
    clamav_backend = normalize_clamav_backend(clamav_cfg.get("backend", "auto"))
    clamav_icon = "✔" if clamav_enabled else "✖"
    clamav_line = f"{clamav_icon} ClamAV integration active <{clamav_backend}>"

    tmpfs_active = bool(tmpfs_state.get("tmpfs_active", False))
    mountpoint = str(
        tmpfs_state.get("mountpoint")
        or (Path.home() / ".dirracuda" / "quarantine_tmpfs")
    )
    tmpfs_icon = "✔" if tmpfs_active else "✖"
    tmpfs_line = f"{tmpfs_icon} tmpfs activated <{mountpoint}>"
    return clamav_line, tmpfs_line
```

### 2. Modify `gui/components/dashboard.py`

**Add import** after existing component imports (near line 43):

```python
from gui.components import dashboard_status
```

**Replace body of `_coerce_bool`** (lines 688–694) — keep decorator and signature, delegate:

```python
@staticmethod
def _coerce_bool(value: Any) -> bool:
    """Convert mixed config values to bool with safe defaults."""
    return dashboard_status.coerce_bool_dashboard(value)
```

**Replace body of `_normalize_clamav_backend`** (lines 697–700) — keep decorator and signature, delegate:

```python
@staticmethod
def _normalize_clamav_backend(value: Any) -> str:
    """Normalize backend mode to one of auto/clamdscan/clamscan."""
    return dashboard_status.normalize_clamav_backend(value)
```

**Replace body of `_compose_runtime_status_lines`** (lines 702–720) — keep signature, resolve defaults here then delegate:

```python
def _compose_runtime_status_lines(
    self,
    clamav_cfg: Optional[Dict[str, Any]] = None,
    tmpfs_state: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    """Build ClamAV/tmpfs status lines shown below console output."""
    clamav_cfg = clamav_cfg if isinstance(clamav_cfg, dict) else self._load_clamav_config()
    tmpfs_state = tmpfs_state if isinstance(tmpfs_state, dict) else get_tmpfs_runtime_state()
    return dashboard_status.compose_runtime_status_lines(clamav_cfg, tmpfs_state)
```

---

## What Stays Untouched

- `DashboardWidget._update_runtime_status_display` — calls `_compose_runtime_status_lines()`, unchanged
- `DashboardWidget._load_clamav_config` — still an instance method on DashboardWidget
- All module-level names (`_logger`, imports) — unchanged
- `DashboardWidget` constructor, public API, attribute names — unchanged
- All existing tests pass through `DashboardWidget._coerce_bool`, `DashboardWidget._compose_runtime_status_lines` — still callable, still same behavior

---

## Verification (run in order)

```bash
# A) Compile check
python3 -m py_compile gui/components/dashboard_status.py gui/components/dashboard.py

# B) Targeted tests (full frozen gate set)
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_theme_runtime_toggle.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_c1_coercion_filesize.py \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_dashboard_reddit_wiring.py -q

# C) Canonical contract import smoke (full frozen gate — matches baseline/C4/C5)
./venv/bin/python -c "
import gui.browsers
import gui.components.unified_browser_window as ubw
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser,
    open_file_viewer, open_image_viewer,
    UnifiedBrowserCore,
    FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
)
from gui.components.dashboard import DashboardWidget
assert hasattr(ubw, 'threading')
assert hasattr(ubw, 'messagebox')
assert hasattr(ubw, 'queue')
assert hasattr(ubw, 'tk')
assert hasattr(ubw, 'ttk')
print('IMPORT SMOKE: PASS')
"

# D) Full regression
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_c6.txt 2>&1; RESULT=$?
cat /tmp/pytest_c6.txt
echo "pytest exit=${RESULT}"
```

Expected: same 2 pre-existing DB failures only; any additional failure = C6 regression.
