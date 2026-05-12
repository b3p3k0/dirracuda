# Roadmap: Pry/RCE Sunset

Status legend: `Not Started` | `In Progress` | `Blocked` | `Done`

## Card Sequence

1. C0 - Contract Freeze + Touchpoint Inventory (`Done`)
2. C1 - Entrypoint + Session-Gate Removal (`Done`)
3. C2 - Pry Runtime Excision (`Done`)
4. C3 - RCE Runtime Excision (`Done`)
5. C4 - Compatibility Cleanup (No Destructive Migration) (`Done`)
6. C5 - Tests + Scenario Matrix Update (`Done`)
7. C6 - Docs Sync + Lessons + Closeout (`Done`)
8. C7 - Full Pry/RCE Artifact Purge + Legacy Config Auto-Migration (`Done`)

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
2. M2: C1-C3 approved (runtime entrypoints removed) (`Done`).
3. M3: C4-C5 approved (compat and tests stabilized) (`Done`).
4. M4: C6 approved (docs synced, lessons recorded, final closeout) (`Done`).
5. M5: C7 approved (artifact purge and breaking legacy config cleanup) (`Done`).

## C6 Execution Note

C6 completed. Updated `README.md` (PyYAML dependency description) and `docs/TECHNICAL_REFERENCE.md` (14 targeted edits: Document Conventions notice, block diagram, directory structure table, shared/ module map, config table `rce`/`pry` rows marked legacy-only, schema notes, server list Pry action, §6.7 Pry section, §7.4 RCE section, §8.2 RCE Signatures section, and glossary entries). All stale "suspended/incomplete" language replaced with "sunset/removed" language; config keys marked as legacy-tolerated rather than active config surface. `ROADMAP.md`, `TASK_CARDS.md`, `LESSONS_LEARNED.md` updated with final card statuses, execution report, and closeout lessons. Guardrail grep on docs produces only intentional sunset-reference hits; zero unexpected active-runtime claims. Runtime code grep remains fully clean. Regression smoke: all targeted suites pass; `test_s10_se_dork_probe_task_lifecycle_success` confirmed pre-existing failure (C4 baseline). Ready for final closeout.

## C7 Execution Note

C7 completed. Removed dormant RCE signature artifacts (`shared/signatures/rce_smb/`, `conf/signatures/rce_smb/*.yaml`) and dropped `PyYAML` from runtime dependencies. Added startup legacy-config auto-migration in `shared/config.py`: detects top-level `pry`/`rce` keys, creates a timestamped backup, rewrites config atomically without those keys, and logs explicit warnings. If rewrite fails, runtime continues with sanitized in-memory config and remediation guidance. Path-service cleanup removed subsystem-only fields/references (`signatures_rce_dir`, `rce_analysis_log_file`, `flat_rce_analysis_log_file`) plus legacy migration ops tied to those paths. DB schema compatibility remains intentionally unchanged. C7 validation passed (compile, targeted tests, regression smoke, guardrail grep), with known pre-existing `test_s10_se_dork_probe_task_lifecycle_success` unchanged and classified as non-regression.

## Blocker Escalation

If blocked by sandbox/tooling/test environment:

1. Record exact blocking condition.
2. Provide exact command(s) for HI to run.
3. State expected result/output for unblock confirmation.
