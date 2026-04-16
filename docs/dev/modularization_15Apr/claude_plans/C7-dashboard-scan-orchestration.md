# C7 — Dashboard Scan Orchestration Extraction

## Context

`gui/components/dashboard.py` is 3317 lines. C1–C6 extracted log display (`dashboard_logs.py`) and runtime-status composition (`dashboard_status.py`). C7 extracts scan orchestration: the queue/lifecycle/stop methods that still live inline in `DashboardWidget`. The pattern established by prior cards is **free functions in a new module that accept `dash` (the DashboardWidget instance) as their first arg**, with thin one-line delegation wrappers left in `dashboard.py`.

---

## Files to change

| File | Action |
|------|--------|
| `gui/components/dashboard_scan.py` | **CREATE** — 22 free functions |
| `gui/components/dashboard.py` | **MOD** — add import + replace method bodies with delegations |

---

## What to extract (22 functions → `dashboard_scan.py`)

### Queue / multi-protocol lifecycle
| dashboard.py method | Lines | dashboard_scan.py function |
|---------------------|-------|---------------------------|
| `_clear_queued_scan_state` | 898–905 | `clear_queued_scan_state(dash)` |
| `_start_unified_scan` | 906–939 | `start_unified_scan(dash, scan_request)` |
| `_build_protocol_scan_options` | 940–1019 | `build_protocol_scan_options(protocol, common_options)` — **pure, no dash arg** |
| `_start_protocol_scan` | 1020–1029 | `start_protocol_scan(dash, protocol, scan_options)` |
| `_abort_queued_scan_on_failure` | 1030–1049 | `abort_queued_scan_on_failure(dash, protocol, reason, *, title=...)` |
| `_launch_next_queued_scan` | 1050–1081 | `launch_next_queued_scan(dash)` |
| `_handle_queued_scan_completion` | 1082–1114 | `handle_queued_scan_completion(dash, results)` |

### Scan dialog entry point

`_show_quick_scan_dialog` (872–891) is **NOT extracted**. It calls `show_unified_scan_dialog` which tests patch as `gui.components.dashboard.show_unified_scan_dialog`. Moving the call site to `dashboard_scan.py` would break that frozen patch path (`test_dashboard_scan_dialog_wiring.py`). The method stays in `dashboard.py` as-is.

### Pre-scan checks
| dashboard.py method | Lines | dashboard_scan.py function |
|---------------------|-------|---------------------------|
| `_ensure_shodan_api_key_for_scan` | 1282–1321 | `ensure_shodan_api_key_for_scan(dash, scan_options)` |
| `_check_external_scans` | 3095–3135 | `check_external_scans(dash)` — calls `dash._validate_external_process()` (not moved) |

### Protocol launch handlers
| dashboard.py method | Lines | dashboard_scan.py function |
|---------------------|-------|---------------------------|
| `_start_new_scan` | 1322–1419 | `start_new_scan(dash, scan_options)` |
| `_start_ftp_scan` | 2906–2943 | `start_ftp_scan(dash, scan_options)` |
| `_start_http_scan` | 2944–2981 | `start_http_scan(dash, scan_options)` |

### Progress handling
| dashboard.py method | Lines | dashboard_scan.py function |
|---------------------|-------|---------------------------|
| `_handle_scan_progress` | 1421–1452 | `handle_scan_progress(dash, percentage, status, phase)` |
| `_show_scan_progress` | 1453–1459 | `show_scan_progress(dash, country)` |
| `_monitor_scan_completion` | 1460–1603 | `monitor_scan_completion(dash)` — nested `check_completion` closure captures `dash` |

### Stop / error handlers
| dashboard.py method | Lines | dashboard_scan.py function |
|---------------------|-------|---------------------------|
| `_stop_scan_immediate` | 3262–3280 | `stop_scan_immediate(dash)` |
| `_stop_scan_after_host` | 3281–3299 | `stop_scan_after_host(dash)` |
| `_handle_stop_error` | 3300–3318 | `handle_stop_error(dash, error_message)` |

### Public progress API (must stay on DashboardWidget, become thin wrappers)
| dashboard.py method | Lines | dashboard_scan.py function |
|---------------------|-------|---------------------------|
| `start_scan_progress` | 804–817 | `start_scan_progress(dash, scan_type, countries)` |
| `update_scan_progress` | 818–845 | `update_scan_progress(dash, percentage, message)` |
| `finish_scan_progress` | 846–871 | `finish_scan_progress(dash, success, results)` |

---

## NOT extracted in C7 (explicit scope boundary)

- `_run_post_scan_batch_operations` — C8 (batch orchestration)
- `_get_servers_for_bulk_ops` — C8
- `_validate_external_process` — helper called via `dash._validate_external_process()` from extracted `check_external_scans`
- `_show_stop_confirmation` / `_handle_stop_choice` — stop UI dialog (not in recommended extraction set)
- All UI build / layout / reddit / config editor methods

---

## `dashboard_scan.py` structure

```python
"""
Scan orchestration helpers for DashboardWidget (C7 extraction).

Each function takes the dashboard instance (dash) as first arg and mirrors
the original method behavior from dashboard.py.  No UI text or behavior changes.
"""

import json
import os
import sys
import time
import tkinter as tk
from typing import Any, Dict, List, Optional

from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


def _mb():
    """Return the messagebox from gui.components.dashboard's namespace.

    Tests patch gui.components.dashboard.messagebox.  Calling messagebox
    through this helper means the patched object is used at call-time,
    preserving all frozen patch paths (e.g. test_dashboard_api_key_gate).
    Falls back to the real safe_messagebox if the dashboard module is not
    yet loaded (e.g. unit tests that import dashboard_scan in isolation).
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox
```

All messagebox calls inside `dashboard_scan.py` use `_mb().showinfo(...)`, `_mb().showerror(...)`, etc. — **never** `messagebox.foo(...)` directly.

---

## Intra-class call discipline (patchability rule)

Inside every extracted function, calls to other DashboardWidget methods **must go through `dash.method_name()`, not `dashboard_scan.function_name(dash)`**. This preserves instance-level monkeypatching (e.g. `monkeypatch.setattr(instance, '_check_external_scans', mock)`) in tests like `test_ftp_scan_dialog`.

Concrete examples of what this means:

| Call site inside extracted fn | Must be written as | NOT as |
|---|---|---|
| check_external_scans in start_new/ftp/http_scan | `dash._check_external_scans()` | `check_external_scans(dash)` |
| ensure_shodan_api_key | `dash._ensure_shodan_api_key_for_scan(opts)` | `ensure_shodan_api_key_for_scan(dash, opts)` |
| monitor_scan_completion | `dash._monitor_scan_completion()` | `monitor_scan_completion(dash)` |
| show_scan_progress | `dash._show_scan_progress(country)` | `show_scan_progress(dash, country)` |
| update_scan_button_state | `dash._update_scan_button_state(state)` | *(stays in dashboard.py, called via dash)* |
| clear_queued_scan_state | `dash._clear_queued_scan_state()` | `clear_queued_scan_state(dash)` |
| abort_queued_scan_on_failure | `dash._abort_queued_scan_on_failure(...)` | `abort_queued_scan_on_failure(dash, ...)` |
| launch_next_queued_scan | `dash._launch_next_queued_scan()` | `launch_next_queued_scan(dash)` |
| build_protocol_scan_options | `dash._build_protocol_scan_options(p, opts)` | `build_protocol_scan_options(p, opts)` |
| start_protocol_scan | `dash._start_protocol_scan(p, opts)` | `start_protocol_scan(dash, p, opts)` |
| reset_log_output | `dash._reset_log_output(country)` | *(stays in dashboard.py, called via dash)* |

**Rule of thumb**: if a method could plausibly be patched in a test (especially ones in `test_ftp_scan_dialog`, `test_dashboard_api_key_gate`, `test_dashboard_scan_dialog_wiring`), always route through `dash.method()`. Only use `_mb()` for messagebox since that is patched at module level, not instance level.


# ── queue/lifecycle helpers ──────────────────────────────────────────────────
def clear_queued_scan_state(dash) -> None: ...
def start_unified_scan(dash, scan_request: dict) -> None: ...
def build_protocol_scan_options(protocol: str, common_options: Dict[str, Any]) -> Dict[str, Any]: ...
def start_protocol_scan(dash, protocol: str, scan_options: Dict[str, Any]) -> bool: ...
def abort_queued_scan_on_failure(dash, protocol, reason, *, title="Protocol Scan Failed") -> None: ...
def launch_next_queued_scan(dash) -> None: ...
def handle_queued_scan_completion(dash, results: Dict[str, Any]) -> None: ...

# ── dialog entry point: NOT extracted (see scope note above) ─────────────────
# show_quick_scan_dialog stays in dashboard.py to preserve patch path

# ── pre-scan checks ──────────────────────────────────────────────────────────
def ensure_shodan_api_key_for_scan(dash, scan_options: Dict[str, Any]) -> bool: ...
def check_external_scans(dash) -> None: ...

# ── protocol launchers ───────────────────────────────────────────────────────
def start_new_scan(dash, scan_options: dict) -> bool: ...
def start_ftp_scan(dash, scan_options: dict) -> bool: ...
def start_http_scan(dash, scan_options: dict) -> bool: ...

# ── progress handling ────────────────────────────────────────────────────────
def handle_scan_progress(dash, percentage, status, phase) -> None: ...
def show_scan_progress(dash, country) -> None: ...
def monitor_scan_completion(dash) -> None: ...

# ── stop / error ─────────────────────────────────────────────────────────────
def stop_scan_immediate(dash) -> None: ...
def stop_scan_after_host(dash) -> None: ...
def handle_stop_error(dash, error_message: str) -> None: ...

# ── public progress API ──────────────────────────────────────────────────────
def start_scan_progress(dash, scan_type: str, countries) -> None: ...
def update_scan_progress(dash, percentage, message: str) -> None: ...
def finish_scan_progress(dash, success: bool, results: Dict[str, Any]) -> None: ...
```

---

## `dashboard.py` changes

1. Add import after existing `dashboard_status` import:
   ```python
   from gui.components import dashboard_scan
   ```

2. Replace each method body with a delegation. Examples:

   ```python
   def _clear_queued_scan_state(self) -> None:
       dashboard_scan.clear_queued_scan_state(self)

   def _build_protocol_scan_options(self, protocol, common_options):
       return dashboard_scan.build_protocol_scan_options(protocol, common_options)

   def start_scan_progress(self, scan_type: str, countries: List[str]) -> None:
       """Start displaying scan progress."""
       dashboard_scan.start_scan_progress(self, scan_type, countries)
   ```

   Public API methods (`start_scan_progress`, `update_scan_progress`, `finish_scan_progress`) keep their docstrings; bodies become single delegation calls.

---

## Critical contract preservation

- `DashboardWidget.start_scan_progress`, `update_scan_progress`, `finish_scan_progress` remain as methods with **identical signatures**.
- `_check_external_scans` + `scan_button_state` lock semantics: the extracted function preserves the exact conditional flow and calls `dash._validate_external_process()` (stays in dashboard.py).
- `_monitor_scan_completion`'s `check_completion` inner closure: extracted as `monitor_scan_completion(dash)` with the same inner function renamed to `_check` — captures `dash` instead of `self`, semantically identical.
- `build_protocol_scan_options` is pure (no `self` state). Extracted without `dash` arg; dashboard.py wrapper passes only `(protocol, common_options)`.

---

## Verification

```bash
# A) Compile
python3 -m py_compile gui/components/dashboard_scan.py gui/components/dashboard.py

# B) Targeted tests
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_ftp_scan_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_theme_runtime_toggle.py \
  gui/tests/test_dashboard_bulk_ops.py \
  -q

# C) Canonical contract import smoke (matches C4/C5/C6)
./venv/bin/python -c "
import gui.browsers
import gui.components.unified_browser_window as ubw
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser,
    open_file_viewer, open_image_viewer,
    UnifiedBrowserCore, FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
)
from gui.components.dashboard import DashboardWidget
assert callable(DashboardWidget.start_scan_progress)
assert callable(DashboardWidget.update_scan_progress)
assert callable(DashboardWidget.finish_scan_progress)
assert hasattr(ubw, 'threading')
assert hasattr(ubw, 'messagebox')
assert hasattr(ubw, 'queue')
assert hasattr(ubw, 'tk')
assert hasattr(ubw, 'ttk')
print('IMPORT SMOKE: PASS')
"

# D) Full regression (expect exactly 2 pre-existing DB failures)
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_c7.txt 2>&1; RESULT=$?
cat /tmp/pytest_c7.txt
echo "pytest exit=${RESULT}"
```

Expected: same 2 pre-existing DB failures only (`test_manual_upsert_*`). Any additional failure = C7 regression.

---

## HI manual gate

1. Start SMB scan → stop it; verify stop-state transitions + retry path.
2. Start FTP scan → verify completion + dashboard refresh.
3. Start HTTP scan → verify completion + dashboard refresh.
4. Confirm progress updates render mid-scan and final results appear in server list.
