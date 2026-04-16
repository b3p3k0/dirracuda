# C8 — Dashboard Batch Ops Extraction

## Context

C7 extracted scan lifecycle into `dashboard_scan.py`. C8 extracts the remaining post-scan batch orchestration (probe/extract/summary/ClamAV dialogs) from `dashboard.py` into a new `dashboard_batch_ops.py`, following the same C7 dash-first free-function pattern.

The critical constraint is test patch-path preservation: multiple tests patch symbols **at the `gui.components.dashboard.*` namespace**. The free functions in the new module must never cache those imports at load-time — they must resolve them at call-time via a `_d()` helper (same mechanism as `_mb()` in C7).

---

## Files Changed

| Action | File |
|--------|------|
| NEW | `gui/components/dashboard_batch_ops.py` |
| MOD | `gui/components/dashboard.py` |

---

## Step 1 — Create `gui/components/dashboard_batch_ops.py`

### Module docstring (mirrors `dashboard_scan.py` style)

Explain: dash-first pattern, `_mb()` for messagebox, `_d()` for other patch-sensitive symbols, intra-class calls go through `dash._method_name()`.

### Imports

```python
import json
import sys
import threading  # annotations only — never instantiate Thread/Event here; use _d("threading") at call-time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk  # annotations + TclError only — never construct widgets here; use _d("tk")/_d("ttk")

from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils import probe_cache
from gui.utils.logging_config import get_logger
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.components.batch_summary_dialog import show_batch_summary_dialog
```

- `threading` **must be imported at module level** — function signatures carry `cancel_event: threading.Event` (dashboard.py:1536, :1913) which are evaluated at import time. Omitting it causes `NameError` on load.
- **At call-time**, all `threading.Thread(...)` and `threading.Event()` instantiation must go through `_d("threading").Thread(...)` / `_d("threading").Event()` to preserve `gui.components.dashboard.threading.*` patch semantics.
- Same split applies to `tkinter as tk`: imported for annotations and `tk.TclError` handling; widget construction (`Toplevel`, `Label`, `Button`) goes through `_d("tk")` / `_d("ttk")`.
- `dispatch_probe_run`, `probe_patterns`, `get_probe_snapshot_path_for_host`, `create_quarantine_dir`, `extract_runner` must NOT be imported — always resolved via `_d()`.
- `ThreadPoolExecutor` / `as_completed` are not patched at dashboard namespace, so they may be imported directly.

### Helper functions

```python
def _mb():
    """Resolve messagebox from gui.components.dashboard namespace at call-time.
    Same pattern as dashboard_scan._mb(). Tests patch gui.components.dashboard.messagebox.*
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _d(name: str):
    """Resolve a name from gui.components.dashboard at call-time.
    Tests patch gui.components.dashboard.<name>; this ensures the patched
    binding is used rather than a cached import-time reference.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None:
        return getattr(mod, name)
    raise RuntimeError(
        f"gui.components.dashboard not yet loaded (looking for {name!r})"
    )
```

### Free functions (14 total)

Each takes `dash` as first arg (except `protocol_label_from_host_type`, which is pure):

| Free function | Patch-sensitive calls | Intra-class calls |
|---|---|---|
| `protocol_label_from_host_type(host_type)` | — | — |
| `protocol_label_for_result(dash, result)` | — | `dash._protocol_label_from_host_type()` |
| `build_probe_notes(dash, share_count, enable_rce, issue_detected, analysis, result)` | — | `dash._handle_rce_status_update()` |
| `load_clamav_config(dash)` | — | `dash.settings_manager` |
| `maybe_show_clamav_dialog(dash, results, clamav_cfg, *, wait, modal)` | local import from `clamav_results_dialog` (not patched at dashboard.*) | — |
| `show_scan_results(dash, results)` | `_mb().showinfo()` for fallback | — |
| `show_batch_summary(dash, results, job_type=None)` | — | `dash._protocol_label_for_result()` |
| `get_servers_for_bulk_ops(dash, skip_indicator_extract, host_type_filter, scan_start_time, scan_end_time)` | — | `dash.db_reader` |
| `run_background_fetch(dash, title, message, fetch_fn)` | `_d("tk").Toplevel`, `_d("tk").Label`, `_d("ttk").Progressbar`, `_d("threading").Thread(...)` | — |
| `probe_single_server(dash, server, max_dirs, max_files, timeout_seconds, enable_rce, cancel_event)` | `_d("dispatch_probe_run")`, `_d("probe_patterns").attach_indicator_analysis`, `_d("get_probe_snapshot_path_for_host")` | `dash._protocol_label_from_host_type()` |
| `execute_batch_probe(dash, servers)` | `_d("tk").Toplevel`, `_d("tk").Label`, `_d("ttk").Progressbar`, `_d("tk").Button`, `_d("threading").Event()`, `_d("threading").Thread(...)` | `dash._probe_single_server()`, `dash._protocol_label_from_host_type()` |
| `extract_single_server(dash, server, max_file_mb, max_total_mb, max_time, max_files, extension_mode, included_extensions, excluded_extensions, quarantine_base_path, cancel_event, clamav_config=None)` | `_d("create_quarantine_dir")`, `_d("extract_runner").run_extract` | `dash._protocol_label_from_host_type()` |
| `execute_batch_extract(dash, servers)` | `_d("tk").Toplevel`, `_d("tk").Label`, `_d("ttk").Progressbar`, `_d("tk").Button`, `_d("threading").Event()` (no Thread — uses ThreadPoolExecutor directly) | `dash._extract_single_server()`, `dash._protocol_label_from_host_type()` |
| `run_post_scan_batch_operations(dash, scan_options, scan_results, *, schedule_reset, show_dialogs)` | `_mb().showerror`, `_mb().showinfo` | `dash._show_scan_results()`, `dash._get_servers_for_bulk_ops()`, `dash._run_background_fetch()`, `dash._execute_batch_probe()`, `dash._execute_batch_extract()`, `dash._protocol_label_from_host_type()`, `dash._show_batch_summary()`, `dash._load_clamav_config()`, `dash._maybe_show_clamav_dialog()` |

Note: `run_post_scan_batch_operations` has `summary_stack: List[Tuple[...]] = []`. `Tuple` is included in the typing imports (missing from dashboard.py — safe because Python doesn't evaluate local variable annotations at runtime, but include it for correctness).

---

## Step 2 — Modify `gui/components/dashboard.py`

### 2a. Add import (line ~45, alongside existing dashboard_* imports)

```python
from gui.components import dashboard_batch_ops
```

### 2b. Preserve all patch-target names in dashboard.py (DO NOT remove)

After extraction, these names in dashboard.py must remain imported — tests patch them at `gui.components.dashboard.*` and they must stay resolvable there:

```python
# Keep all of these — do not treat them as "unused" and remove them:
from gui.utils import safe_messagebox as messagebox          # patched: messagebox.showerror/showinfo
import tkinter as tk                                         # patched: tk.Toplevel/Label/Button
from tkinter import ttk                                      # patched: ttk.Progressbar
import threading                                             # patched: threading.Thread
from gui.utils import probe_patterns, extract_runner         # patched: probe_patterns.attach_indicator_analysis, extract_runner.run_extract
from gui.utils.probe_cache_dispatch import (
    get_probe_snapshot_path_for_host, dispatch_probe_run     # patched individually
)
from shared.quarantine import create_quarantine_dir          # patched: create_quarantine_dir
```

### 2c. Replace each of the 14 method bodies with a one-liner wrapper

Pattern:
```python
def _run_post_scan_batch_operations(self, scan_options, scan_results, *,
                                     schedule_reset=True, show_dialogs=True):
    dashboard_batch_ops.run_post_scan_batch_operations(
        self, scan_options, scan_results,
        schedule_reset=schedule_reset, show_dialogs=show_dialogs,
    )

def _get_servers_for_bulk_ops(self, skip_indicator_extract=True,
                               host_type_filter=None, scan_start_time=None,
                               scan_end_time=None):
    return dashboard_batch_ops.get_servers_for_bulk_ops(
        self, skip_indicator_extract=skip_indicator_extract,
        host_type_filter=host_type_filter,
        scan_start_time=scan_start_time, scan_end_time=scan_end_time,
    )

def _run_background_fetch(self, title, message, fetch_fn):
    return dashboard_batch_ops.run_background_fetch(self, title, message, fetch_fn)

def _execute_batch_probe(self, servers):
    return dashboard_batch_ops.execute_batch_probe(self, servers)

def _probe_single_server(self, server, max_dirs, max_files,
                          timeout_seconds, enable_rce, cancel_event):
    return dashboard_batch_ops.probe_single_server(
        self, server, max_dirs, max_files, timeout_seconds, enable_rce, cancel_event
    )

def _protocol_label_from_host_type(self, host_type):
    return dashboard_batch_ops.protocol_label_from_host_type(host_type)

def _protocol_label_for_result(self, result):
    return dashboard_batch_ops.protocol_label_for_result(self, result)

def _build_probe_notes(self, share_count, enable_rce, issue_detected, analysis, result):
    return dashboard_batch_ops.build_probe_notes(
        self, share_count, enable_rce, issue_detected, analysis, result
    )

def _execute_batch_extract(self, servers):
    return dashboard_batch_ops.execute_batch_extract(self, servers)

def _extract_single_server(self, server, max_file_mb, max_total_mb, max_time,
                             max_files, extension_mode, included_extensions,
                             excluded_extensions, quarantine_base_path,
                             cancel_event, clamav_config=None):
    return dashboard_batch_ops.extract_single_server(
        self, server, max_file_mb, max_total_mb, max_time, max_files,
        extension_mode, included_extensions, excluded_extensions,
        quarantine_base_path, cancel_event, clamav_config=clamav_config,
    )

def _show_batch_summary(self, results, job_type=None):
    dashboard_batch_ops.show_batch_summary(self, results, job_type=job_type)

def _load_clamav_config(self):
    return dashboard_batch_ops.load_clamav_config(self)

def _maybe_show_clamav_dialog(self, results, clamav_cfg, *, wait=False, modal=False):
    dashboard_batch_ops.maybe_show_clamav_dialog(
        self, results, clamav_cfg, wait=wait, modal=modal
    )

def _show_scan_results(self, results):
    dashboard_batch_ops.show_scan_results(self, results)
```

---

## Validation (run in order)

```bash
# A) Compile check
python3 -m py_compile gui/components/dashboard_batch_ops.py gui/components/dashboard.py

# B) Targeted C8 tests
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_extract_runner_clamav.py \
  gui/tests/test_clamav_results_dialog.py \
  -q

# C) Contract import smoke
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
assert callable(DashboardWidget._run_post_scan_batch_operations)
assert callable(DashboardWidget._get_servers_for_bulk_ops)
assert callable(DashboardWidget._execute_batch_extract)
assert hasattr(ubw, 'threading')
assert hasattr(ubw, 'messagebox')
assert hasattr(ubw, 'queue')
assert hasattr(ubw, 'tk')
assert hasattr(ubw, 'ttk')
print('IMPORT SMOKE: PASS')
"

# D) Full regression
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_c8.txt 2>&1; RESULT=$?
cat /tmp/pytest_c8.txt
echo "pytest exit=${RESULT}"
```

Expected: same 2 pre-existing DB failures, no new failures.
