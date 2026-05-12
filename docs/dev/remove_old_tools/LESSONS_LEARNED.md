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
7. **Follow-up needed:** None — C6 completed the docs sync.

---

1. **Date:** 2026-05-12
2. **Card:** C6 — Docs Sync + Lessons + Closeout
3. **What changed:** Updated `README.md` PyYAML dependency description. Applied 14 targeted edits to `docs/TECHNICAL_REFERENCE.md`: replaced "suspended/incomplete" language with "sunset/removed in C3/C2"; removed `rce_analyzer.py` from directory table; split `signatures/rce_smb/` into `conf/` (data) and `shared/` (loader) rows; marked `rce`/`pry` config rows as legacy-only; updated §6.7 Pry and §7.4 RCE sections; replaced §8.2 "Adding RCE Signatures" with historical-artifact note; updated glossary entries for YAML and Pry. Updated ROADMAP.md card statuses (C1–C4 `Done`) and milestones. Appended C6 execution report and final validation summary to TASK_CARDS.md.
4. **Root cause prevented:** "Suspended" vs "removed" are different claims — leaving "suspended/incomplete" language after full removal implies the feature could be re-enabled, which creates false belief in operational availability and a misleading attack surface description.
5. **Regression caught/avoided:** Guardrail grep on `README.md` + `TECHNICAL_REFERENCE.md` produced zero unexpected active-runtime claims. Regression smoke suites all pass. Pre-existing `test_s10_se_dork_probe_task_lifecycle_success` failure correctly classified and not masked.
6. **New guardrail added:** When verifying a dependency (e.g., PyYAML), grep for actual usage across `shared/`, `gui/`, `cli/` before updating the dep description — the loader module at `shared/signatures/rce_smb/loader.py` remained a legitimate consumer even after the scanner was removed. "Suspended" language in docs is a signal that a runtime-removal card has not been fully closed out; always update docs on the same card that removes the code, or explicitly defer to a named follow-up card.
7. **Follow-up needed:** `docs/guides/RCE_SIGNATURE_GUIDE.md` still exists and references the removed scanner pipeline; can be removed or archived in a future cleanup pass. `test_s10_se_dork_probe_task_lifecycle_success` is a pre-existing failure unrelated to this sunset — track separately.

---

1. **Date:** 2026-05-12
2. **Card:** C7 — Full Pry/RCE Artifact Purge + Legacy Config Auto-Migration
3. **What changed:** Removed dormant signature artifacts and tests (`shared/signatures/rce_smb/*`, `conf/signatures/rce_smb/*.yaml`, `shared/tests/test_signature_loader_paths.py`), removed `PyYAML` from `requirements.txt`, and updated README/technical docs accordingly. Added startup migration in `shared/config.py` to strip top-level legacy `pry`/`rce` keys with timestamped backup + atomic rewrite. Added fallback path that keeps sanitized in-memory config when rewrite fails. Removed path-service fields/migration ops tied only to the removed subsystem (`signatures_rce_dir`, `rce_analysis_log_file`, `flat_rce_analysis_log_file`).
4. **Root cause prevented:** Leaving dormant artifacts and "tolerated forever" config keys creates permanent maintenance drag and misleading dependency/documentation claims.
5. **Regression caught/avoided:** New migration tests cover both success and failure paths, ensuring runtime behavior is clean even when filesystem writes fail.
6. **New guardrail added:** For breaking config cleanup, always pair destructive key removal with assisted migration (backup + atomic rewrite + sanitized in-memory fallback) so behavior is deterministic and recoverable.
7. **Follow-up needed:** Pre-existing `test_s10_se_dork_probe_task_lifecycle_success` remains unrelated and tracked separately.
