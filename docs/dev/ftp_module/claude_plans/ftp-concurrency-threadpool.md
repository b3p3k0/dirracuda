# Slice 8A — dashboard.py Phase 2 Scan-Controls Extraction

## Context

`gui/components/dashboard.py` is 1929 lines and the top production hotspot in the refactor
series. The scan-controls cluster (status bar, button state machine, stop/start handlers, external
scan detection) is a self-contained block occupying lines 1445–1929. Extracting it to a dedicated
mixin follows the identical pattern used for `_DashboardBulkOpsMixin`
(`gui/components/dashboard_bulk_ops.py`). Zero behavior changes — methods move verbatim.

---

## Files Changed

| File | Action |
|------|--------|
| `gui/components/dashboard_scan_controls.py` | **Create** — new mixin |
| `gui/components/dashboard.py` | **Edit** — import + class decl + remove 23 methods |
| `gui/tests/test_dashboard_scan_controls.py` | **Create** — targeted regression tests |

---

## Step 1 — Create `gui/components/dashboard_scan_controls.py`

### Module structure (mirror `dashboard_bulk_ops.py` pattern exactly)

```python
"""
DashboardWidget scan-controls mixin.

Extracted from dashboard.py to keep that module's line count manageable.
Provides status-bar management, scan button state machine, start/stop
handlers, and external-scan detection as a private mixin class consumed
only by DashboardWidget.  Do not import or instantiate directly.
"""

import tkinter as tk
from tkinter import messagebox
import time
import os

from gui.components.ftp_scan_dialog import show_ftp_scan_dialog
from gui.components.http_scan_dialog import show_http_scan_dialog
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")
```

> **Note — no `import json` at module level.** `_check_external_scans` already has an inline
> `import json` inside its body; moving it verbatim keeps that. Adding a second module-level
> import would be redundant noise with no benefit.


class _DashboardScanControlsMixin:
    """
    Private mixin providing scan-controls methods for DashboardWidget.

    Relies on the following attributes being set by DashboardWidget.__init__:
        self.parent               - tk root / parent widget
        self.main_frame           - primary container frame
        self.theme                - theme object with apply_to_widget(), fonts, colors
        self.scan_button          - tk.Button for SMB scans
        self.scan_button_state    - str state ("idle", "scanning", "stopping", …)
        self.ftp_scan_button      - tk.Button for FTP scans (may be None)
        self.http_scan_button     - tk.Button for HTTP scans (may be None)
        self.external_scan_pid    - int PID of detected external scan (may be None)
        self.stopping_started_time - float or None, used by stop-timeout logic
        self.scan_manager         - ScanManager instance
        self.backend_interface    - BackendInterface instance
        self.config_path          - path to SMBSeek config.json (may be None)
        self.current_scan_options - dict of active scan options (may be None)
        self._mock_mode_notice_shown - bool flag for one-time mock warning
        self.current_progress_summary - str (may be "")
        self.settings_manager     - SettingsManager instance
    """
```

### Methods to paste verbatim (in order, no changes except indentation inside class)

All 23 methods from dashboard.py lines 1445–1929:

1. `_build_status_bar` (lines 1445–1461)
2. `_show_status_bar` (lines 1462–1467)
3. `_hide_status_bar` (lines 1468–1471)
4. `_handle_scan_button_click` (lines 1473–1492)
5. `_handle_ftp_scan_button_click` (lines 1494–1507)
6. `_handle_http_scan_button_click` (lines 1509–1522)
7. `_maybe_warn_mock_mode_persistence` (lines 1524–1535)
8. `_start_ftp_scan` (lines 1537–1570)
9. `_start_http_scan` (lines 1572–1605)
10. `_update_scan_button_state` (lines 1607–1650)
11. `_set_button_to_start` (lines 1652–1658)
12. `_set_button_to_stop` (lines 1660–1666)
13. `_set_button_to_disabled` (lines 1668–1674)
14. `_set_button_to_stopping` (lines 1676–1685)
15. `_set_button_to_retry` (lines 1687–1695)
16. `_set_button_to_error` (lines 1697–1703)
17. `_check_external_scans` (lines 1707–1746)  ← note: has inline `import json` inside body; leave verbatim
18. `_validate_external_process` (lines 1748–1764)
19. `_show_stop_confirmation` (lines 1768–1861)
20. `_handle_stop_choice` (lines 1863–1870)
21. `_stop_scan_immediate` (lines 1874–1891)
22. `_stop_scan_after_host` (lines 1893–1910)
23. `_handle_stop_error` (lines 1912–1929)

> Note: `_check_external_scans` has `import json` inline in its body — leave it as-is
> (verbatim rule), even though `json` is also in the module-level imports.

---

## Step 2 — Edit `gui/components/dashboard.py`

### 2a — Imports block changes (lines 34–38 region)

**Remove** these three now-unused imports (each function only appears in the 23 moved methods):
```python
from gui.components.ftp_scan_dialog import show_ftp_scan_dialog   # line 34
from gui.components.http_scan_dialog import show_http_scan_dialog  # line 35
from gui.utils.dialog_helpers import ensure_dialog_focus           # line 38
```

**Add** in their place (or adjacent to the `dashboard_bulk_ops` import):
```python
from gui.components.dashboard_scan_controls import _DashboardScanControlsMixin
```

> **Patch-target consequence:** removing these imports means any test that patches
> `gui.components.dashboard.show_ftp_scan_dialog` will break because that attribute
> no longer exists on the module. See Step 3a below.

### 2b — Class declaration (line ~44)

Change:
```python
class DashboardWidget(_DashboardBulkOpsMixin):
```
To:
```python
class DashboardWidget(_DashboardBulkOpsMixin, _DashboardScanControlsMixin):
```

### 2c — Remove method bodies

Delete the entire block from the first line of `_build_status_bar` through the last line of
`_handle_stop_error` (current lines 1445–1929, ~484 lines). Leave no trailing blank lines
beyond a single separator.

Expected post-edit line count: ~1445 lines (1929 − 484 = 1445).

---

## Step 3a — Fix broken patch targets in `gui/tests/test_ftp_scan_dialog.py`

Lines 446 and 458 currently patch `gui.components.dashboard.show_ftp_scan_dialog`.
After the import is removed from `dashboard.py`, Python resolves the name at call-site
from the mixin module, so the patch target must change.

**Change both patch decorators / `monkeypatch.setattr` calls** from:
```
gui.components.dashboard.show_ftp_scan_dialog
```
to:
```
gui.components.dashboard_scan_controls.show_ftp_scan_dialog
```

Verify with Gate 6 (`test_ftp_scan_dialog.py`).

---

## Step 3b — Create `gui/tests/test_dashboard_scan_controls.py`

Small focused regression tests. Use `DashboardWidget.__new__(DashboardWidget)` and
inject stub attributes — same pattern as `test_dashboard_bulk_ops.py`.

### Test cases

| Test | What it covers |
|------|---------------|
| `test_update_state_idle_enables_ftp_http_buttons` | `_update_scan_button_state("idle")` sets `scan_button_state = "idle"`, calls `_set_button_to_start`, re-enables ftp/http buttons |
| `test_update_state_scanning_disables_ftp_http_buttons` | `"scanning"` disables both side buttons |
| `test_update_state_disabled_external_disables_all` | `"disabled_external"` disables all three buttons |
| `test_handle_scan_button_click_idle_triggers_check_and_dialog` | idle state → calls `_check_external_scans` + `_show_quick_scan_dialog` |
| `test_handle_scan_button_click_scanning_triggers_stop_dialog` | scanning state → calls `_show_stop_confirmation` |
| `test_check_external_scans_no_active_scan_goes_idle` | `scan_manager.is_scan_active()` returns False → `_update_scan_button_state("idle")` |
| `test_check_external_scans_external_pid_goes_disabled` | external PID detected + process valid → `"disabled_external"` |
| `test_handle_stop_error_scan_stopped_goes_idle` | `scan_manager.is_scanning` is False → `_update_scan_button_state("idle")` |
| `test_handle_stop_error_scan_still_running_goes_error` | `scan_manager.is_scanning` is True → `_update_scan_button_state("error")` |

---

## Validation Gates (run in order)

```bash
# Gate 1 — syntax
./venv/bin/python -m py_compile gui/components/dashboard.py gui/components/dashboard_scan_controls.py

# Gate 2 — import smoke test
./venv/bin/python -c "from gui.components.dashboard import DashboardWidget; print('OK')"

# Gate 3 — start-scan path tests
./venv/bin/python -m pytest gui/tests/test_dashboard_start_scan_paths.py -q

# Gate 4 — theme toggle
./venv/bin/python -m pytest gui/tests/test_theme_runtime_toggle.py -q

# Gate 5 — bulk ops
./venv/bin/python -m pytest gui/tests/test_dashboard_bulk_ops.py gui/tests/test_dashboard_bulk_ops_helpers.py -q

# Gate 6 — FTP scan dialog (needs display)
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_ftp_scan_dialog.py -q

# Gate 7 — action routing + server list
./venv/bin/python -m pytest gui/tests/test_action_routing.py gui/tests/test_server_list_card4.py -q

# Gate 8 — new focused tests
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_controls.py -q

# Gate 9 — line count
wc -l gui/components/dashboard.py   # target: ≤ 1500
```

---

## Completion Report Template

```
Before: dashboard.py  1929 lines
After:  dashboard.py  ~1445 lines   dashboard_scan_controls.py  ~540 lines

Files changed:
  created  gui/components/dashboard_scan_controls.py
  modified gui/components/dashboard.py
  created  gui/tests/test_dashboard_scan_controls.py

Gate results:
  Gate 1 (py_compile)         PASS
  Gate 2 (import smoke)       PASS
  Gate 3 (start_scan_paths)   PASS
  Gate 4 (theme_toggle)       PASS
  Gate 5 (bulk_ops)           PASS
  Gate 6 (ftp_scan_dialog)    PASS
  Gate 7 (action_routing)     PASS
  Gate 8 (scan_controls)      PASS
  Gate 9 (wc -l ≤ 1500)       PASS

Total tests passed: N
Behavior unchanged / no UI contract changes.
```
