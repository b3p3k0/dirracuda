# Lessons Learned: Pry/RCE Sunset

Use this log to prevent repeat failures and preserve guardrails for future agents.

## Seed Guardrails

1. Remove root causes, not only UI toggles.
2. Delete dead paths from runtime wiring, not just presentation layers.
3. Preserve legacy compatibility first; never assume schema shape.
4. Keep changes surgical and card-scoped to simplify rollback and blame.
5. Use deterministic grep + targeted tests to prove sunset completeness.
6. Monitor file sizes continuously; pause for modularization before risk compounds.
7. Keep docs synchronized with actual code behavior before closeout.

## Common Pitfalls To Avoid

1. Removing a dialog file that also hosts shared generic components.
2. Dropping DB artifacts prematurely and breaking existing installations.
3. Deleting test coverage without adding replacement assertions for absence behavior.
4. Leaving stale config accessors that silently no-op but confuse maintainers.

## Entry Template (Append Per Card)

1. Date:
2. Card:
3. What changed:
4. Root cause prevented:
5. Regression caught/avoided:
6. New guardrail added:
7. Follow-up needed:

---

1. **Date:** 2026-05-12
2. **Card:** C5 — Tests + Scenario Matrix Update
3. **What changed:** Removed `PryOperationsHarness` class and its orphaned `ServerListWindowBatchOperationsMixin` import from `_server_ops_harness.py`. Stripped stale `_rce_unlocked`, `rce_enabled_var`, `show_rce_controls`, `_set_pry_status_button_visible` fixture residue from 5 test files. Removed `"pry"` from fuzz job-type pool in `test_server_ops_fuzz_sequences.py`. Removed `"rce_enabled": False` from 4 preflight fixture dicts. Added `test_s3_pry_sunset_no_pry_methods_on_batch_mixin` and `test_rce_enabled_absent_from_scan_request` as permanent regression guards.
4. **Root cause prevented:** Stale fixture attributes (`_rce_unlocked` on DashboardWidget stub, `show_rce_controls` on UnifiedScanDialog factory) would silently pass even after re-introduction of removed symbols — producing false confidence in the sunset.
5. **Regression caught/avoided:** `test_s10_se_dork_probe_task_lifecycle_success` was failing at the C4 baseline, not introduced by C5. Stash-and-rerun confirmed pre-existing status. Classify, don't mask.
6. **New guardrail added:** Request-shape assertion (`"rce_enabled" not in request`) is more reliable than `hasattr` checks for instance variables. Class-level `hasattr` assertions on mixin methods catch re-introduction at the right layer — avoid checking instance state when the behavioral invariant lives on the class.
7. **Follow-up needed:** C6 docs sync — update `README.md` and `docs/TECHNICAL_REFERENCE.md` to remove suspended Pry/RCE language; verify PyYAML dep entry before removing.
