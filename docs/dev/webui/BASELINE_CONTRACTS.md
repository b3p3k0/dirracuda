# Baseline Contracts

Recorded by DA (Claude) at start of C0. RA (Codex) reviews before C1 begins.

Branch: `feature/secure-webui`
Date: 2026-05-09

No product source files were changed to produce this document.

---

## 1. Git Status (execution-time)

```
## feature/secure-webui
```

Branch is clean. Planning-phase snapshot showed staged docs files; those were
committed in 47ee18e before C0 ran.

---

## 2. Experimental Feature Tab Order

Source: `gui/components/experimental_features/registry.py` `_get_features()`

### Current (pre-C7)

| Index | feature_id | Label     |
|-------|-----------|-----------|
| 0     | se_dork   | SearXNG   |
| 1     | reddit    | Reddit    |
| 2     | dorkbook  | Dorkbook  |
| 3     | keymaster | Keymaster |

`Web UI` tab is absent. This is expected; it is added in C7.

### Target after C7

| Index | feature_id | Label     |
|-------|-----------|-----------|
| 0     | se_dork   | SearXNG   |
| 1     | reddit    | Reddit    |
| 2     | webui     | Web UI    |
| 3     | dorkbook  | Dorkbook  |
| 4     | keymaster | Keymaster |

C7 acceptance criterion: tab order must match the target exactly.

---

## 3. Likely-Touched File Line Counts

Collected with `wc -l` at execution time.

```
    64 gui/components/experimental_features/registry.py
   152 gui/components/experimental_features_dialog.py
   377 gui/components/experimental_features/se_dork_tab.py
   132 gui/components/experimental_features/reddit_tab.py
    62 gui/components/experimental_features/dorkbook_tab.py
    61 gui/components/experimental_features/keymaster_tab.py
   875 gui/tests/test_experimental_features_dialog.py
    40 gui/main.py
  1700 dirracuda
   239 shared/workflow.py
   901 gui/utils/backend_interface/interface.py
  1289 gui/utils/scan_manager.py
    44 requirements.txt
  5936 total
```

**Hard limit notice:** `dirracuda` is exactly 1700 lines - the per-file hard limit.
Any card that touches `dirracuda` must either confirm zero net line growth or
propose modularization before editing.

---

## 4. Canonical Entrypoint And Shim Confirmation

### `./dirracuda` - canonical runtime entrypoint

```
#!/usr/bin/env python3
"""
Dirracuda - GUI

A cross-platform graphical interface for the Dirracuda security toolkit.
```

Confirmed: this is the product runtime entrypoint. All cards preserve it.

### `gui/main.py` - import-compatible shim only

```
#!/usr/bin/env python3
"""
Dirracuda - Legacy Entry Point Compatibility Shim.

This module is import-compatible only.
Runtime launch must use ``./dirracuda``.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Ensure project root is importable for direct `python gui/main.py` invocation.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from gui.utils.dirracuda_loader import get_canonical_gui_class


# Backward-compatible import surface:
# from gui.main import SMBSeekGUI
SMBSeekGUI = get_canonical_gui_class()

_DEPRECATION_MESSAGE = (
    "gui/main.py is a legacy compatibility shim and is not a supported runtime "
    "entrypoint. Launch Dirracuda with ./dirracuda."
)
```

Confirmed: 40 lines, import-only. Contains no `main()` or `if __name__ ==
"__main__"` runtime block. Must not be made runnable by any implementation card.

---

## 5. Test Command Conventions

Standard runner: `./venv/bin/python -m pytest`
Quick lane: `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick`

`xvfb-run`: available at `/usr/bin/xvfb-run`

GUI tests that create Tk windows should be run as:
`xvfb-run -a ./venv/bin/python -m pytest <target> -q`

---

## 6. Baseline Test Results

### Test 1 - Experimental features dialog

Command:
```
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

Result:
```
............................................                             [100%]
44 passed in 0.18s
```

Status: **PASS** - clean baseline.

### Test 2 - Quick lane

Command:
```
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

Abbreviated failure excerpt from output:
```
+ /home/kevin/DEV/dirracuda/venv/bin/python -m pytest -m scenario or fuzz gui/tests -q
...................................................F..                   [100%]
=================================== FAILURES ===================================
________________ test_s10_se_dork_probe_task_lifecycle_success _________________

...

    browser._on_probe_selected()

>       assert fake_conn.commit_calls == 1
E       assert 0 == 1
E        +  where 0 = <...FakeSeDorkConnection object at 0x...>.commit_calls

gui/tests/test_server_ops_scenario_matrix.py:429: AssertionError
=================================== short test summary info ============================
FAILED gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success
1 failed, 53 passed, 1100 deselected in 0.94s
```

Exit code: 1

Status: **1 pre-existing failure** - `test_s10_se_dork_probe_task_lifecycle_success`.

---

## 7. Pre-Existing Failure Detail

**Test:** `gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success`

**Assertion:**
```python
assert fake_conn.commit_calls == 1
# actual: fake_conn.commit_calls == 0
```

**Root cause (observed):** `_on_probe_selected()` calls `update_result_probe` via a
monkeypatched stub that never calls `commit()` on the connection, and the real
store path that would call `commit()` is also stubbed out. `FakeSeDorkConnection`
counts commits but receives none in this test path.

**Pre-existing status:** Yes. No web UI code exists on this branch. This failure
was present before C0 ran and is unrelated to the web UI implementation work.

**Unblock path for RA/HI triage (before or during C1):**

Option A - Fix the harness: update `FakeSeDorkConnection` in
`gui/tests/_server_ops_harness.py` to increment `commit_calls` when
`update_result_probe` is invoked (if the store is supposed to own the commit).

Option B - Fix the production path: locate where `open_connection` is used in
`experimental/se_dork/` and confirm a `commit()` call is missing after
`update_result_probe`.

C0 does not fix this failure. RA/HI should disposition it before C1 closes.

---

## 8. New Files Created By C0

| File | Lines |
|------|-------|
| `docs/dev/webui/BASELINE_CONTRACTS.md` | 234 |

No product source files were changed. No commit was made.
