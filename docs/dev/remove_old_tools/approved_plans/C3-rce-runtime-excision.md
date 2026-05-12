# C3 — RCE Runtime Excision

## Context

C3 removes the RCE (Remote Code Execution vulnerability scanner) opt-in experimental feature from all runtime paths. The feature was guarded by `_rce_unlocked` / `--check-rce` and spanned: CLI argument parsing → workflow → access operation → `rce_analyzer` → `rce_scanner` module → GUI toggles/callbacks → server list status presentation → probe dispatch hub. All of it is excised. No DB schema changes — C4 handles column cleanup. The scanner module (`shared/rce_scanner/`) and its signature files are dead weight once the call chain is gone.

---

## Step 1 — Delete files entirely

Deleted via `git rm`:

| File | Reason |
|------|--------|
| `commands/access/rce_analyzer.py` | Sole purpose: RCE analysis dispatch |
| `shared/rce_scanner/__init__.py` | RCE module |
| `shared/rce_scanner/fact_collector.py` | RCE module |
| `shared/rce_scanner/logger.py` | RCE module |
| `shared/rce_scanner/probes.py` | RCE module |
| `shared/rce_scanner/reporter.py` | RCE module |
| `shared/rce_scanner/scanner.py` | RCE module |
| `shared/rce_scanner/scorer.py` | RCE module |
| `shared/rce_scanner/verdicts.py` | RCE module |
| `shared/signatures/rce_smb/rules.py` | RCE rule engine |
| `conf/signatures/rce_smb/CVE-2020-1206.yaml` | RCE signature definition |
| `shared/tests/test_probe_gating.py` | 100% tests `shared.rce_scanner.probes`/`.verdicts` |
| `shared/tests/test_smb_parsing.py` | 100% tests `shared.rce_scanner.*` |
| `shared/tests/test_verdict_conditions.py` | 100% tests `RuleEngine` from deleted `rules.py` |

**Coordinated package edit:** `shared/signatures/rce_smb/__init__.py` — removed `from .rules import RuleEngine` and `"RuleEngine"` from `__all__`. `SignatureLoader` and `SignatureValidator` unchanged. `test_signature_loader_paths.py` unaffected.

---

## Step 2 — CLI / Workflow / Backend surgery

### `cli/smbseek.py`
- Removed `--check-rce` `add_argument` block.

### `shared/workflow.py`
- Removed `getattr(args, 'check_rce', False)` from `AccessOperation(...)` constructor call (6-arg → 5-arg).

### `commands/access/operation.py`
- Removed `rce_analyzer` from `from . import ...`
- Removed `check_rce=False` param and `self.check_rce = check_rce` assignment
- Deleted `_analyze_rce_vulnerabilities()` method
- Deleted RCE `SafeProbeRunner` init block (including `self._probe_runner`)
- Deleted both `if self.check_rce:` call sites

---

## Step 3 — GUI scan dialogs / dashboard surgery

### `gui/dashboard/widget.py`
- Removed `show_rce_controls=False,` kwarg from `show_unified_scan_dialog(...)` call.

### `gui/components/unified_scan_dialog.py`
- Removed `show_rce_controls` constructor param + assignment
- Removed `rce_enabled_var`
- Removed settings load/persist blocks gated on `show_rce_controls`
- Removed `"rce_enabled"` from form state capture and scan request
- Removed RCE state apply block
- Removed RCE checkbox/spacer UI creation block
- Removed `show_rce_controls` from factory functions

### `gui/components/scan_dialog.py`
- Same pattern as `unified_scan_dialog.py`

### `gui/components/scan_dialog_layout.py`
- Removed `"rce_enabled"` from form state capture and restore
- Deleted `_create_rce_analysis_option()` method
- Deleted `_create_hidden_rce_spacer_option()` method
- Removed both method names from bind list

### `gui/components/scan_preflight.py`
- Removed `rce_enabled` extraction
- Removed RCE line from probe summary
- Removed RCE validation block
- Changed `if not any((probe_enabled, extract_enabled, rce_enabled)):` → `if not any((probe_enabled, extract_enabled)):`

### `gui/components/dashboard_scan.py`
- Removed RCE session gate block
- Removed `rce_enabled` extraction
- Removed `"rce_enabled": rce_enabled` from all three protocol option dicts

### `gui/components/dashboard_batch_ops.py`
- Removed `enable_rce` load/gate block
- Removed `enable_rce` from executor submit args
- Removed `enable_rce: bool = False` from `_probe_single_server()` signature
- Removed `enable_rce=enable_rce` from `dispatch_probe_run()` call
- Removed `enable_rce` param and RCE result block from `build_probe_notes()`

### `gui/utils/probe_runner.py`
- Removed `enable_rce_analysis: bool = False` parameter
- Removed entire 52-line RCE analysis block

---

## Step 4 — Server list window surgery

### `gui/components/server_list_window/window.py`
- Removed `self._rce_unlocked`
- Removed `show_rce_column=self._rce_unlocked` from table constructor
- Removed `rce_status_callback` and `show_rce_controls=self._rce_unlocked` from details popup

### `gui/components/server_list_window/details.py`
- Removed `rce_status_callback` and `show_rce_controls` params
- Removed `show_rce_details` threading through render/format chain
- Removed `enable_rce` from `dispatch_probe_run()` call
- Removed RCE analysis enable logic block
- Removed RCE result handling and callback block
- Removed RCE checkbox from probe dialog
- Removed RCE preference storage
- Removed RCE override params from `_start_probe()`

### `gui/components/server_list_window/table.py`
- Deleted `RCE_STATUS_EMOJI` and `RCE_STATUS_TEXT` dicts
- Removed `show_rce_column` param
- Removed `"rce"` column from columns tuple and all display logic

### `gui/components/server_list_window/actions/batch.py`
- Removed RCE unlock gate block
- Removed `rce_unlocked` + `enable_rce` locals
- Removed `enable_rce` from `dispatch_probe_run()` call
- Removed RCE analysis result handling block and `_handle_rce_status_update()` call

### `gui/components/server_list_window/actions/batch_operations.py`
- Removed RCE settings block from probe dialog
- Removed `enable_rce` override parameter from batch probe call

### `gui/components/server_list_window/actions/batch_status.py`
- Removed RCE status attachment lines from `_attach_probe_status()`
- Deleted `_determine_rce_status()`, `_handle_rce_status_update()`, `_rce_status_to_emoji()` methods

---

## Step 5 — Minimal test surgery

### Deleted:
- `shared/tests/test_probe_gating.py` — 100% RCE
- `shared/tests/test_smb_parsing.py` — 100% RCE
- `shared/tests/test_verdict_conditions.py` — 100% RCE

### Edited (minimal surgery):
- `shared/tests/test_access_auth_retry_and_failhard.py` — removed `check_rce=False,` from `_make_operation()` factory
- `gui/tests/test_dashboard_scan_dialog_wiring.py` — removed 2 `assert captured["show_rce_controls"] is False` lines
- `gui/tests/test_action_routing.py` — deleted `test_rce_status_update_by_row_key_matches_only_correct_row` and `test_probe_smb_rce_is_forced_off_when_session_locked`
- `gui/tests/test_unified_scan_dialog_validation.py` — deleted `test_build_scan_request_forces_rce_disabled_when_controls_hidden` (discovered during Step 6 validation run)

---

## Step 6 — Validation results

| Check | Result |
|-------|--------|
| Allowlist (35 paths: 14 D, 21 M) | ✓ |
| Line count policy ≤1700 (largest: `dashboard_batch_ops.py` at 1487) | ✓ |
| `py_compile` (16 source files) | ✓ |
| `test_signature_loader_paths` | 3 passed |
| `test_unified_scan_dialog_validation` | 18 passed (1 RCE test removed) |
| `test_scan_preflight_probe_depth` | 2 passed |
| `test_dashboard_scan_dialog_wiring` | 5 passed |
| `test_action_routing` | passed (2 RCE tests removed) |
| `test_server_ops_scenario_matrix` | 1 known pre-existing failure (`test_s10_se_dork_probe_task_lifecycle_success` — se_dork lifecycle, unrelated to C3) |
| `test_access_auth_retry_and_failhard` | 5 passed |
| Guardrail grep residuals | All DB schema/migration (C4-deferred), config fields, or false positives (`_coerce_status_code`) |

---

## Step 7 — Remediation: missed probe_cache_dispatch call site

**Gate decision after Step 6: REJECT — two files missed in original scope.**

`gui/utils/probe_cache_dispatch.py:dispatch_probe_run()` still passed `"enable_rce_analysis": enable_rce` into `probe_runner.run_probe()`, which C3 had already stripped of that parameter. Any live SMB probe flow would have thrown `TypeError: run_probe() got an unexpected keyword argument 'enable_rce_analysis'`. The companion test `test_dispatch_smb_kwargs_forwarding` asserted the forwarding and was also missed.

### `gui/utils/probe_cache_dispatch.py`
- Removed `enable_rce: bool = False` from `dispatch_probe_run()` signature.
- Removed `"enable_rce_analysis": enable_rce,` from the SMB `_kwargs` dict.

### `gui/tests/test_probe_cache_dispatch.py`
- Updated docstring to remove `enable_rce_analysis,`.
- Removed `enable_rce=True,` from `dispatch_probe_run(...)` call.
- Removed `assert kw["enable_rce_analysis"] is True`.
- All other forwarding assertions (`allow_empty`, `db_accessor`, `cancel_event`, `max_depth`) preserved.

### Step 7 validation results

| Check | Result |
|-------|--------|
| Allowlist (`probe_cache_dispatch.py`, `test_probe_cache_dispatch.py` show M) | ✓ |
| `py_compile probe_cache_dispatch.py probe_runner.py` | ✓ |
| `test_probe_cache_dispatch` | 28 passed |
| Full C3 targeted suite (101 tests total) | 100 passed, 1 known pre-existing se_dork failure |
| Guardrail grep `enable_rce_analysis\|enable_rce` in both files | no hits |

---

## Allowed residuals (C4-deferred)

- `shared/database.py`, `shared/db_migrations.py` — `rce_status` column DDL and `upsert_rce_status` method
- `gui/utils/database_access_*.py` — SQL queries and write methods for the `rce_status` DB column
- `gui/utils/default_gui_settings.py` — `'rce_enabled': False` default setting key
- `shared/config.py` — `is_rce_enabled_by_default()` accessor
- Test fixtures that include `"rce_status"` as a DB record field shape

Schema cleanup, config key removal, and DB accessor pruning are C4 scope.

---

## HI test required before merge

1. **Scan dialog smoke test** — open the GUI, launch a scan dialog (both dashboard quick-scan and server list probe dialog). Verify no exceptions on open, no RCE checkbox visible anywhere.
2. **Batch probe run** — run a batch probe on ≥1 server entry. Confirm the server list table renders without an RCE column and the probe result card shows no RCE status field.
