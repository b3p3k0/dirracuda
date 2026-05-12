# Roadmap: Pry/RCE Sunset

Status legend: `Not Started` | `In Progress` | `Blocked` | `Done`

## Card Sequence

1. C0 - Contract Freeze + Touchpoint Inventory (`Done`)
2. C1 - Entrypoint + Session-Gate Removal (`Not Started`)
3. C2 - Pry Runtime Excision (`Not Started`)
4. C3 - RCE Runtime Excision (`Not Started`)
5. C4 - Compatibility Cleanup (No Destructive Migration) (`Not Started`)
6. C5 - Tests + Scenario Matrix Update (`Done`)
7. C6 - Docs Sync + Lessons + Closeout (`Not Started`)

## Gate Policy

1. Only one card can be active at a time.
2. Next card starts only after PA/RA approval of:
- scope conformance
- validation evidence
- line-count rubric check
- residual risk note

## C0 Execution Note

C0 completed. Full 7-subsystem touchpoint matrix written into `TASK_CARDS.md`. Two new risks (R-07, R-08) logged in `RISK_REGISTER.md`. Case-insensitive `rg` pass identified `gui/utils/wordlist_path.py` (missed by case-sensitive search) and confirmed one RCE reference in `README.md` (line 82). All file line counts logged; no file exceeds 1800 lines. Ready for C1 gate review.

## C5 Execution Note

C5 completed. Removed stale Pry/RCE fixture code across 8 test files: deleted `PryOperationsHarness` class and its now-unused `ServerListWindowBatchOperationsMixin` import from `_server_ops_harness.py`; removed `"pry"` from fuzz job-type choices; stripped `_rce_unlocked`, `rce_enabled_var`, `show_rce_controls`, and `_set_pry_status_button_visible` stub residue; removed `"rce_enabled": False` from preflight fixture dicts. Added two sunset regression tests: `test_s3_pry_sunset_no_pry_methods_on_batch_mixin` (asserts `_on_pry_selected` and `_execute_pry_target` absent from mixin layer) and `test_rce_enabled_absent_from_scan_request` (builds real scan request, asserts `"rce_enabled"` key absent). All 8 targeted suites pass. `test_s10_se_dork_probe_task_lifecycle_success` confirmed pre-existing failure at C4 baseline; not a C5 regression. Guardrail grep clean — only intentional compat residuals remain (`pry_status_dialog` stubs, schema DDL fixtures). Ready for C6 gate review.

## Delivery Milestones

1. M1: C0 approved — scope and touchpoint matrix frozen (`Done`).
2. M2: C1-C3 approved (runtime entrypoints removed).
3. M3: C4-C5 approved (compat and tests stabilized).
4. M4: C6 approved (docs synced, lessons recorded, final closeout).

## Blocker Escalation

If blocked by sandbox/tooling/test environment:

1. Record exact blocking condition.
2. Provide exact command(s) for HI to run.
3. State expected result/output for unblock confirmation.
