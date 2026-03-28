# ClamAV Optional Integration - Task Cards

Date: 2026-03-27
Status: Draft
Execution model: one small issue/card at a time, explicit PASS/FAIL gates.

## Locked Intent

1. Phase 1 scope: bulk extract only.
2. ClamAV integration is optional and must not break bulk extract when disabled.
3. Failure policy default: fail-open.
4. Clean files are moved to extracted root.
5. Infected files move to quarantine known-bad subtree.
6. Summary dialog appears after bulk extract and can be muted for current session only.

## Card C0: Contract Inventory + Baseline (Plan Only)

Issue:
- We need exact contract mapping before touching multi-entry bulk extract paths.

Scope:
- Planning artifacts only.

Deliverables:
1. `docs/dev/clamav_intergation/SPEC_DRAFT.md`
2. `docs/dev/clamav_intergation/TASK_CARDS.md`
3. `docs/dev/clamav_intergation/ASCII_SKETCHES.md`
4. `docs/dev/clamav_intergation/RESEARCH_2026-03-27.md`

Acceptance:
1. Bulk extract entry points are mapped.
2. Locked decisions and residual open decisions are explicit.
3. Includes known-failure prevention checklist.

Validation:
```bash
rg -n "_execute_batch_extract|_extract_single_server|run_extract\(" gui/components gui/utils
```

HI test needed:
- No.

## Card C1: ClamAV Backend Adapter (Code)

Issue:
- No reusable scanner abstraction exists; backend/tool selection and parsing would otherwise be duplicated.

Scope:
- Add scanner adapter supporting `auto`, `clamdscan`, and `clamscan`.

Likely files:
1. New: `shared/clamav_scanner.py`
2. New tests: `shared/tests/test_clamav_scanner.py`

Acceptance:
1. Deterministic result classification: `clean`, `infected`, `error`.
2. Backend selection rules are explicit and test-covered.
3. Timeout/missing-binary behavior is explicit and test-covered.
4. Adapter has no GUI coupling.

Validation:
```bash
python3 -m py_compile shared/clamav_scanner.py
./venv/bin/python -m pytest shared/tests/test_clamav_scanner.py -q
```

HI test needed:
- No (unit-level).

## Card C2: Reusable Quarantine Post-Processor Seam (Code)

Issue:
- We need phase-1 bulk support without blocking future browser-download integration.

Scope:
- Introduce a post-processing contract used by bulk extract now and reusable later.

Likely files:
1. New: `shared/quarantine_postprocess.py` (or equivalent)
2. `gui/utils/extract_runner.py`
3. New tests: `shared/tests/test_quarantine_postprocess.py`

Acceptance:
1. Contract input/output shape is stable and documented.
2. Bulk extract path uses contract.
3. No behavior drift when ClamAV is disabled.

Validation:
```bash
python3 -m py_compile shared/quarantine_postprocess.py gui/utils/extract_runner.py
./venv/bin/python -m pytest shared/tests/test_quarantine_postprocess.py -q
```

HI test needed:
- No.

## Card C3: Bulk Extract Integration + Summary Schema (Code)

Issue:
- Bulk extract currently writes quarantine files only; no AV pipeline exists.

Scope:
- Integrate optional scan calls in bulk extract path and extend summary JSON.

Likely files:
1. `gui/utils/extract_runner.py`
2. `gui/components/dashboard.py`
3. `gui/components/server_list_window/actions/batch.py`
4. New tests: `gui/tests/test_extract_runner_clamav.py`

Acceptance:
1. Disabled path remains backward-compatible.
2. Enabled path scans each downloaded file.
3. Summary contains stable `clamav` block with totals and itemized outcomes.
4. Scanner errors do not crash bulk extract in fail-open mode.

Validation:
```bash
python3 -m py_compile gui/utils/extract_runner.py gui/components/dashboard.py gui/components/server_list_window/actions/batch.py
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -q
```

HI test needed:
- Yes.
- Steps:
1. Enable ClamAV in config.
2. Run post-scan bulk extract on known clean sample hosts.
3. Confirm summary shows scan counts and no hard-fail on scanner issues.

## Card C4: Promotion + Known-Bad Routing (Code)

Issue:
- Requested workflow requires clean promotion and explicit known-bad placement.

Scope:
- Move clean files to extracted root and infected files to known-bad quarantine subtree.

Likely files:
1. `shared/quarantine.py` (or new helper module)
2. `gui/utils/extract_runner.py`
3. New tests: `shared/tests/test_quarantine_promotion.py`

Acceptance:
1. Clean files move to `~/.dirracuda/extracted/<host>/<date>/<share>/...`.
2. Infected files move to `~/.dirracuda/quarantine/known_bad/<host>/<date>/<share>/...`.
3. Scanner-error files remain in original quarantine path.
4. Activity logs and summary include destination decisions.

Validation:
```bash
python3 -m py_compile shared/quarantine.py gui/utils/extract_runner.py
./venv/bin/python -m pytest shared/tests/test_quarantine_promotion.py -q
```

HI test needed:
- Yes.
- Steps:
1. Include EICAR in extract target set.
2. Run bulk extract with ClamAV enabled.
3. Verify clean files under extracted root and EICAR under known-bad path.

## Card C5: ClamAV Results Dialog + Session Mute (Code)

Issue:
- Operators need readable AV outcomes without popup fatigue.

Scope:
- Add shared results dialog and in-memory session mute state.

Likely files:
1. New: `gui/components/clamav_results_dialog.py`
2. New: `gui/utils/session_flags.py`
3. `gui/components/dashboard.py`
4. `gui/components/server_list_window/actions/batch.py`
5. New tests: `gui/tests/test_clamav_results_dialog.py`

Acceptance:
1. Dialog appears after bulk extract operations when AV is enabled and results exist.
2. "Mute until restart" suppresses further dialogs in current process.
3. Mute resets on app restart.
4. Existing bulk extract messaging remains coherent.

Validation:
```bash
python3 -m py_compile gui/components/clamav_results_dialog.py gui/components/dashboard.py gui/components/server_list_window/actions/batch.py
./venv/bin/python -m pytest gui/tests/test_clamav_results_dialog.py -q
```

HI test needed:
- Yes.
- Steps:
1. Run post-scan bulk extract and server-list batch extract.
2. Confirm dialog appears before mute.
3. Enable mute and verify suppression in same session.
4. Restart app and confirm dialog appears again.

## Card C6: Config Integration (Expanded GUI Controls)

Issue:
- User prefers GUI-managed settings and phase 1 needs more than a single toggle.

Scope:
- Add ClamAV controls to app config dialog and persist safely.

Likely files:
1. `gui/components/app_config_dialog.py`
2. `conf/config.json.example`
3. `shared/config.py`
4. New tests: `gui/tests/test_app_config_dialog_clamav.py`

Acceptance:
1. Controls persist and reload correctly.
2. Missing `clamav` section is created safely.
3. Existing config keys remain intact.
4. Disabled behavior remains unchanged.

Recommended controls:
- enable checkbox
- backend selector
- timeout seconds
- extracted root path
- known-bad subfolder name
- show-results toggle

Validation:
```bash
python3 -m py_compile gui/components/app_config_dialog.py shared/config.py
./venv/bin/python -m pytest gui/tests/test_app_config_dialog_clamav.py -q
```

HI test needed:
- Yes.
- Steps:
1. Change each ClamAV setting via dialog.
2. Save and reopen dialog to verify persistence.
3. Confirm runtime behavior aligns with saved settings.

## Card C7: Full Validation + Rollback Drill

Issue:
- Bulk-path integration needs end-to-end evidence and rollback clarity.

Scope:
- Run automated + manual validation and capture rollback procedure.

Deliverables:
1. `docs/dev/clamav_intergation/VALIDATION_REPORT.md`
2. `docs/dev/clamav_intergation/ROLLBACK_RUNBOOK.md`

Acceptance:
1. Commands and outcomes recorded with PASS/FAIL.
2. Includes clean, infected, and scanner-error scenarios.
3. Rollback path verified and documented.

Validation:
```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
python3 -m py_compile gui/utils/extract_runner.py shared/clamav_scanner.py gui/components/app_config_dialog.py
```

HI test needed:
- Yes (final sign-off).

## Known-Failure Prevention Checklist

- [ ] Verify runtime path for both bulk extract entry points (dashboard and server-list batch).
- [ ] Keep disabled-path behavior unchanged.
- [ ] Treat scanner-unavailable and timeout as explicit states, not generic failures.
- [ ] Keep placement deterministic (clean/extracted, infected/known_bad, errors/quarantine).
- [ ] Confirm no UI-thread blocking during scan/promotion.
- [ ] Validate session mute reset on app restart.

