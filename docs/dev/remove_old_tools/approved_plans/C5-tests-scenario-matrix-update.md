# C5 Plan: Tests + Scenario Matrix Update

## Context

C0–C4 removed the Pry/RCE runtime from the dirracuda codebase. Several test files still carry stale fixture attributes, stub setup lines, and scenario tests that reference removed symbols (`_pry_unlocked`, `_rce_unlocked`, `rce_enabled_var`, `show_rce_controls`, `PryOperationsHarness`, `"pry"` job type). This card cleans those up, adds sunset assertions where the card calls for them, and documents residual intentional compat references.

Branch: `development` — baseline: `ecc5777` (C4 complete)

---

## Residual Classification (Guardrail Grep Results)

### CHANGE — stale fixture/setup code that no longer maps to runtime behavior

| File | Line(s) | Symbol | Action |
|---|---|---|---|
| `test_clamav_results_dialog.py` | 436 | `stub._set_pry_status_button_visible = MagicMock()` | Remove line |
| `_server_ops_harness.py` | 368–391 | `PryOperationsHarness` class + `_pry_unlocked` | Delete entire class |
| `test_server_ops_fuzz_sequences.py` | 66 | `"pry"` in `rng.choice(["probe", "extract", "pry"])` | Drop `"pry"` from list |
| `test_unified_scan_dialog_validation.py` | 73, 89, 93 | `show_rce_controls` param, `rce_enabled_var`, `show_rce_controls` attr | Remove all three; add sunset assertion |
| `test_dashboard_scan_dialog_wiring.py` | 43, 83 | `dash._rce_unlocked = True/False` | Remove both lines (attr gone from DashboardWidget) |
| `test_action_routing.py` | 17, 242 | Stale `_on_pry_selected` comment; `self._rce_unlocked = True` in stub | Remove both |
| `test_scan_preflight_probe_depth.py` | 83, 126, 176, 225 | `"rce_enabled": False` in scan_options fixture dicts | Remove all four occurrences |
| `test_server_ops_scenario_matrix.py` | — | Pry scenario was already removed (C2); no test exists yet | Add `test_s3_pry_sunset_job_type_not_registered()` |

### KEEP — intentional legacy-compat references (do not touch)

| File | Symbol | Reason |
|---|---|---|
| `test_db_tools_engine*.py` | `source TEXT DEFAULT 'pry'` / `VALUES (..., 'pry')` | Schema fixture: historical rows stay readable |
| `test_ftp_state_tables.py` | `rce_status`, `rce_verdict_summary` column assertions | Column still exists in DB; compat policy |
| `test_database_access_protocol_writes.py` | `rce_status TEXT DEFAULT 'not_run'` in DDL fixture | Schema fixture only |
| `test_database_access_protocol_union.py` | same | Same |
| `test_action_routing.py` | lines 41, 71 — `pry_status_dialog` module stubs | `pry_status_dialog.py` still exists (shared by probe/extract/reddit/se_dork) |
| `test_server_list_card4.py` | lines 60, 701, 713 — `pry_status_dialog` stubs | Same reason |

---

## Implementation Steps

### 1. `gui/tests/test_clamav_results_dialog.py` (471 lines)

Remove line 436 only:
```python
# REMOVE:
stub._set_pry_status_button_visible = MagicMock()
```

### 2. `gui/tests/_server_ops_harness.py` (641 lines)

Delete `PryOperationsHarness` class block (lines 368–391 inclusive, plus the blank line after 367). Class is defined only there and not imported anywhere else.

Also remove the now-unused `ServerListWindowBatchOperationsMixin` from the import at lines 14–16. `BatchStatusHarness` inherits from `ServerListWindowBatchStatusMixin` (different class); nothing else in the file uses `ServerListWindowBatchOperationsMixin` once `PryOperationsHarness` is gone.

Before (lines 14–16):
```python
from gui.components.server_list_window.actions.batch_operations import (
    ServerListWindowBatchOperationsMixin,
```
After: remove that entire import statement (or just the `ServerListWindowBatchOperationsMixin` line if it's a multi-name import).

### 3. `gui/tests/test_server_ops_fuzz_sequences.py` (319 lines)

Line 66: `rng.choice(["probe", "extract", "pry"])` → `rng.choice(["probe", "extract"])`

### 4. `gui/tests/test_unified_scan_dialog_validation.py` (392 lines)

In `_make_dialog` factory (lines 73–101):
- Remove `show_rce_controls: bool = True` parameter → `_make_dialog()` (no params)
- Remove `dlg.rce_enabled_var = _Var(False)` (line 89)
- Remove `dlg.show_rce_controls = show_rce_controls` (line 93)

All call sites already call `_make_dialog()` with no arguments — no call-site changes needed.

Add sunset assertion test after existing `test_no_live_max_results_clamp_method_exists`. `rce_enabled_var` was an instance var, not a class var, so `hasattr(UnifiedScanDialog, ...)` would be vacuously true even before removal. Use request-shape assertion instead — call `_build_scan_request()` and assert the key is absent from the returned dict:

```python
def test_rce_enabled_absent_from_scan_request(monkeypatch):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state",
        lambda *_a, **_k: None,
    )
    dlg = _make_dialog()
    request = dlg._build_scan_request()
    assert "rce_enabled" not in request
```

`_make_dialog()` sets `protocol_smb_var=True` by default, satisfying the protocol-required guard in `_build_scan_request`. The `persist_query_budget_state` monkeypatch follows the pattern already used in `test_inline_max_results_vars_flow_into_build_scan_request`.

### 5. `gui/tests/test_dashboard_scan_dialog_wiring.py` (90 lines)

Remove `dash._rce_unlocked = True` (line 43) and `dash._rce_unlocked = False` (line 83). The `_show_quick_scan_dialog` method no longer reads this attribute; tests still pass since assertions check callback keys only.

### 6. `gui/tests/test_action_routing.py` (1355 lines)

- Remove line 17 from the module docstring: `- _on_pry_selected: F row shows warning, pry never launched`
- Remove line 242: `self._rce_unlocked = True` from `_BatchMixinStub.__init__` (attr no longer exists in the mixin)

### 7. `gui/tests/test_scan_preflight_probe_depth.py` (240 lines)

Remove `"rce_enabled": False,` from `scan_options` dicts at lines 83, 126, 176, 225. `ScanPreflightController` no longer reads this key; extra keys in the dict are harmless but misleading.

### 8. `gui/tests/test_server_ops_scenario_matrix.py` (509 lines)

Add sunset assertion test to replace the removed `test_s3_pry_mixed_selection_blocks_launch`. Insert before `test_s4_running_task_count_sync_across_dashboard_and_server_list` (line 103).

The previous draft was too weak — it only checked valid-type creation and `hasattr(harness, "_pry_unlocked")`, which doesn't prove Pry paths are removed. Use a class-level assertion on `ServerListWindowBatchOperationsMixin` instead: this directly verifies the C2 removal is permanent and would catch any re-introduction.

```python
@pytest.mark.scenario
def test_s3_pry_sunset_no_pry_methods_on_batch_mixin() -> None:
    """Regression: Pry dispatch methods must not exist on batch mixin layer post-C2."""
    from gui.components.server_list_window.actions.batch_operations import (
        ServerListWindowBatchOperationsMixin,
    )
    from gui.components.server_list_window.actions.batch import (
        ServerListWindowBatchMixin,
    )
    assert not hasattr(ServerListWindowBatchOperationsMixin, "_on_pry_selected"), (
        "_on_pry_selected was removed in C2; re-introduction would be a regression"
    )
    assert not hasattr(ServerListWindowBatchMixin, "_execute_pry_target"), (
        "_execute_pry_target was removed in C2; re-introduction would be a regression"
    )
```

`_on_pry_selected` lived on `ServerListWindowBatchOperationsMixin` (actions/batch_operations.py); `_execute_pry_target` lived on `ServerListWindowBatchMixin` (actions/batch.py). Both confirmed absent via grep.

---

## Critical Files

- [gui/tests/test_clamav_results_dialog.py](gui/tests/test_clamav_results_dialog.py) — line 436 only
- [gui/tests/_server_ops_harness.py](gui/tests/_server_ops_harness.py) — lines 368–391
- [gui/tests/test_server_ops_fuzz_sequences.py](gui/tests/test_server_ops_fuzz_sequences.py) — line 66
- [gui/tests/test_unified_scan_dialog_validation.py](gui/tests/test_unified_scan_dialog_validation.py) — lines 73, 89, 93 + new test
- [gui/tests/test_dashboard_scan_dialog_wiring.py](gui/tests/test_dashboard_scan_dialog_wiring.py) — lines 43, 83
- [gui/tests/test_action_routing.py](gui/tests/test_action_routing.py) — lines 17, 242
- [gui/tests/test_scan_preflight_probe_depth.py](gui/tests/test_scan_preflight_probe_depth.py) — lines 83, 126, 176, 225
- [gui/tests/test_server_ops_scenario_matrix.py](gui/tests/test_server_ops_scenario_matrix.py) — new test before line 103

No file exceeds 1700 lines. No modularization needed.

---

## Verification

Run in order:

```bash
# 1. Compile all touched modules
./venv/bin/python -m py_compile \
  gui/tests/test_action_routing.py \
  gui/tests/test_server_ops_scenario_matrix.py \
  gui/tests/test_unified_scan_dialog_validation.py \
  gui/tests/test_server_ops_fuzz_sequences.py \
  gui/tests/test_clamav_results_dialog.py \
  gui/tests/_server_ops_harness.py \
  gui/tests/test_scan_preflight_probe_depth.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py

# 2. Targeted test runs
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_server_ops_scenario_matrix.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog_validation.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_server_ops_fuzz_sequences.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_clamav_results_dialog.py -q
./venv/bin/python -m pytest shared/tests/test_ftp_state_tables.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_scan_preflight_probe_depth.py -q

# 3. Guardrail grep (report remaining residuals and classify)
rg -n -i "pry|_pry_unlocked|PryDialog|rce_enabled|_rce_unlocked|show_rce_controls|rce_status_callback|check-rce|rce_scanner" \
  gui/tests shared/tests docs/dev/remove_old_tools --glob '*.py' --glob '*.md'
```

Expected: all tests pass; guardrail grep shows only intentional compat residuals (schema fixtures, `pry_status_dialog` stubs).

---

## Docs Updates (after validation passes)

- `docs/dev/remove_old_tools/TASK_CARDS.md` — append C5 execution report
- `docs/dev/remove_old_tools/ROADMAP.md` — set C5 status to Done
- `docs/dev/remove_old_tools/LESSONS_LEARNED.md` — append C5 lessons
