# Modularization Refactor - Task Cards (Claude-Ready)

Date: 2026-04-15  
Execution model: one card at a time, no card merging unless HI explicitly approves.

## Global Rules (All Cards)

1. Reproduce/confirm issue first.
2. Apply smallest safe fix (surgical edits only).
3. Run targeted validation for touched components.
4. Report exact commands with PASS/FAIL.
5. No commit unless HI explicitly says `commit`.
6. If blocked, report blocker + exact human unblock commands + expected result.

## Completion Semantics (Required)

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Required Response Format (Per Card)

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

---

## C0 - Contract Freeze + Baseline (Plan Only)

Goal:
Produce a concrete implementation plan against current repo state before moving code.

Scope:
1. Inventory API contracts for:
   - `gui.components.dashboard.DashboardWidget`
   - `gui.components.unified_browser_window` public symbols
2. Inventory patch-sensitive test assumptions (module-path monkeypatch sites).
3. Capture baseline validation command set for browser/dashboard refactor cards.
4. Create `docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md`.

Definition of done:
1. No code edits.
2. Explicit risk list and fallback plan per upcoming card.
3. Clear PASS/FAIL gates for C1-C10.

Validation:
```bash
git status --short
rg -n "DashboardWidget|open_ftp_http_browser|open_smb_browser|FtpBrowserWindow|HttpBrowserWindow|SmbBrowserWindow" gui
```

HI test needed:
- No.

---

## C1 - Shared Utility Consolidation (Coercion + File Size)

Goal:
Centralize duplicated coercion and file-size formatting helpers without behavior changes.

Scope:
1. Add `gui/utils/coercion.py`.
2. Add `gui/utils/filesize.py`.
3. Replace duplicate helper bodies with imports/adapters in scoped modules only.
4. Add focused unit tests for helper equivalence and bounds behavior.

Primary touch targets:
1. `gui/utils/coercion.py` (new)
2. `gui/utils/filesize.py` (new)
3. `gui/components/dashboard.py`
4. `gui/components/unified_browser_window.py`
5. `gui/components/file_viewer_window.py`
6. `gui/tests/*` (targeted additions)

Definition of done:
1. Duplicate helper behavior is preserved exactly.
2. No UI text or flow changes.
3. Coercion logic is explicit and safe for mixed runtime input types.

Validation:
```bash
python3 -m py_compile gui/utils/coercion.py gui/utils/filesize.py gui/components/dashboard.py gui/components/unified_browser_window.py gui/components/file_viewer_window.py
./venv/bin/python -m pytest gui/tests/test_dashboard_runtime_status_lines.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_smb_virtual_root.py -q
```

HI test needed:
- No.

---

## C2 - Browser Package Scaffold + Compatibility Exports

Goal:
Introduce modular browser package boundaries without runtime behavior changes.

Scope:
1. Create `gui/browsers/` package skeleton.
2. Add package-level exports for current browser public symbols.
3. Keep `gui/components/unified_browser_window.py` as compatibility entrypoint.
4. Add import-contract tests to ensure old imports still resolve.

Primary touch targets:
1. `gui/browsers/__init__.py` (new)
2. `gui/components/unified_browser_window.py`
3. `gui/tests/test_*` (import contract coverage)

Definition of done:
1. Existing call sites still import from `gui.components.unified_browser_window` unchanged.
2. Package scaffold exists for phased extraction.

Validation:
```bash
python3 -m py_compile gui/browsers/__init__.py gui/components/unified_browser_window.py
./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
```

HI test needed:
- No.

---

## C3 - Extract UnifiedBrowserCore

Goal:
Move shared browser base class and shared helpers out of monolith.

Scope:
1. Create `gui/browsers/core.py` for `UnifiedBrowserCore` + shared helper logic.
2. Keep protocol subclasses in current location for this card.
3. Preserve lazy import behavior and heavy dependency boundaries.

Primary touch targets:
1. `gui/browsers/core.py` (new)
2. `gui/components/unified_browser_window.py`
3. `gui/browsers/__init__.py`

Definition of done:
1. `UnifiedBrowserCore` behavior unchanged.
2. FTP/HTTP/SMB subclasses continue to function.
3. No import-time impacket regressions.

Validation:
```bash
python3 -m py_compile gui/browsers/core.py gui/components/unified_browser_window.py
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_action_routing.py -q
```

HI test needed:
- No.

---

## C4 - Extract FTP + HTTP Browser Modules

Goal:
Move FTP/HTTP browser classes into dedicated modules while preserving factories and behavior.

Scope:
1. Add `gui/browsers/ftp_browser.py`.
2. Add `gui/browsers/http_browser.py`.
3. Re-export classes through compatibility module.
4. Keep path semantics, cancellation behavior, and concurrency controls unchanged.

Primary touch targets:
1. `gui/browsers/ftp_browser.py` (new)
2. `gui/browsers/http_browser.py` (new)
3. `gui/components/unified_browser_window.py`
4. `gui/browsers/__init__.py`

Definition of done:
1. FTP and HTTP browser tests pass with unchanged expected behavior.
2. Existing entrypoints still route correctly.

Validation:
```bash
python3 -m py_compile gui/browsers/ftp_browser.py gui/browsers/http_browser.py gui/components/unified_browser_window.py
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_action_routing.py gui/tests/test_browser_clamav.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open one FTP host via Server List and verify browse/view/download still work.
2. Open one HTTP host and verify browse/view/download still work.

---

## C5 - Extract SMB Browser + Factory Layer

Goal:
Move SMB browser implementation and open-factory functions into modular package with compatibility shim.

Scope:
1. Add `gui/browsers/smb_browser.py`.
2. Add `gui/browsers/factory.py` for `open_ftp_http_browser` and `open_smb_browser`.
3. Convert `gui/components/unified_browser_window.py` into a stable compatibility re-export module.
4. Preserve `_extract_smb_banner` behavior.

Primary touch targets:
1. `gui/browsers/smb_browser.py` (new)
2. `gui/browsers/factory.py` (new)
3. `gui/components/unified_browser_window.py`
4. `gui/browsers/__init__.py`

Definition of done:
1. All legacy imports still valid.
2. SMB browse factory behavior unchanged.
3. Class/function monkeypatch targets used in tests remain reachable.

Validation:
```bash
python3 -m py_compile gui/browsers/smb_browser.py gui/browsers/factory.py gui/components/unified_browser_window.py
./venv/bin/python -m pytest gui/tests/test_smb_virtual_root.py gui/tests/test_smb_browser_window.py gui/tests/test_action_routing.py gui/tests/test_browser_clamav.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open one SMB host from Server List and verify browse/up/view/download behavior.
2. Confirm banner text still renders correctly when Shodan data is present.

---

## C6 - Dashboard Runtime Status + Shared Helpers Extraction

Goal:
Extract dashboard runtime-status and shared helper logic into dedicated module(s) with no UX changes.

Scope:
1. Add `gui/components/dashboard_status.py` (or `gui/dashboard/status.py`).
2. Move `_coerce_bool`, backend normalization, runtime status composition/update logic.
3. Keep `DashboardWidget` API unchanged.

Primary touch targets:
1. `gui/components/dashboard_status.py` (new)
2. `gui/components/dashboard.py`
3. `gui/tests/test_dashboard_runtime_status_lines.py`

Definition of done:
1. Runtime status lines are identical for all known states.
2. No change to scan start/stop behavior.

Validation:
```bash
python3 -m py_compile gui/components/dashboard_status.py gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_dashboard_runtime_status_lines.py gui/tests/test_theme_runtime_toggle.py -q
```

HI test needed:
- No.

---

## C7 - Dashboard Scan Orchestration Extraction

Goal:
Modularize scan orchestration/queue/stop control logic while preserving parser and lock semantics.

Scope:
1. Add dedicated scan-control module(s) for:
   - unified scan dispatch
   - queued protocol sequencing
   - scan lifecycle monitoring + stop/retry states
2. Keep parser/output contract unchanged.
3. Keep one-active-scan lock semantics unchanged.

Primary touch targets:
1. `gui/components/dashboard_scan.py` (new)
2. `gui/components/dashboard.py`
3. `gui/utils/scan_manager.py` (only if required for injection/adapters)

Definition of done:
1. Scan start/stop/retry behavior is unchanged for SMB/FTP/HTTP.
2. No external scan lock regressions.
3. Progress/status callbacks still parse and display correctly.

Validation:
```bash
python3 -m py_compile gui/components/dashboard_scan.py gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_api_key_gate.py gui/tests/test_dashboard_runtime_status_lines.py -q
```

HI test needed:
- Yes.
- Steps:
1. Start SMB scan, then stop it; verify stop-state transitions.
2. Start FTP scan; verify completion and dashboard refresh.
3. Start HTTP scan; verify completion and dashboard refresh.

---

## C8 - Dashboard Batch Ops Extraction

Goal:
Extract post-scan batch probe/extract orchestration to dedicated modules while preserving scope rules.

Scope:
1. Add dedicated batch operations module(s).
2. Preserve scan-cohort scoping and row-based eligibility logic.
3. Preserve ClamAV dialog routing and summary ordering behavior.

Primary touch targets:
1. `gui/components/dashboard_batch_ops.py` (new)
2. `gui/components/dashboard.py`
3. `gui/utils/extract_runner.py` (only if adapter surface needed)

Definition of done:
1. Bulk probe/extract behavior remains unchanged.
2. Known batch-scope safeguards remain intact.
3. ClamAV completion behavior remains unchanged.

Validation:
```bash
python3 -m py_compile gui/components/dashboard_batch_ops.py gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_dashboard_bulk_ops.py gui/tests/test_extract_runner_clamav.py gui/tests/test_clamav_results_dialog.py -q
```

HI test needed:
- Yes.
- Steps:
1. Run a scan with post-scan bulk probe enabled and confirm scoped behavior.
2. Run a scan with post-scan bulk extract enabled and confirm summary dialogs.

---

## C9 - Dashboard Package Boundary + Compatibility Shim

Goal:
Finalize dashboard modular boundary and preserve legacy import path.

Scope:
1. Introduce `gui/dashboard/` package with explicit exports.
2. Keep `gui/components/dashboard.py` as compatibility shim exposing `DashboardWidget`.
3. Ensure all existing imports and tests keep working.

Primary touch targets:
1. `gui/dashboard/__init__.py` (new)
2. `gui/dashboard/widget.py` (new)
3. `gui/components/dashboard.py`

Definition of done:
1. Legacy import path (`gui.components.dashboard`) still works unchanged.
2. New modular package is canonical for future work.
3. No behavioral changes in runtime paths.

Validation:
```bash
python3 -m py_compile gui/dashboard/__init__.py gui/dashboard/widget.py gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_bulk_ops.py gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:
- Yes.
- Steps:
1. Launch app and verify dashboard opens normally.
2. Verify Start Scan, Servers, DB Tools, Config, and Reddit Grab actions.

---

## C10 - Final Regression + Evidence Pack

Goal:
Close modularization phase with explicit evidence and residual-risk accounting.

Scope:
1. Run final targeted regression suite for touched browser/dashboard flows.
2. Record before/after line-count report for decomposed modules.
3. Publish final validation report with explicit PASS/FAIL and manual-gate status.

Primary touch targets:
1. `docs/dev/modularization_15Apr/VALIDATION_REPORT.md` (new)
2. `docs/dev/modularization_15Apr/ROADMAP.md` (status update)
3. `docs/dev/modularization_15Apr/TASK_CARDS.md` (status update)

Definition of done:
1. Automated gates fully reported.
2. Manual gates explicitly marked PASS or PENDING.
3. Residual risks and follow-up tasks documented.

Validation:
```bash
python3 -m py_compile gui/components/dashboard.py gui/components/unified_browser_window.py gui/browsers/*.py gui/dashboard/*.py
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_smb_virtual_root.py gui/tests/test_action_routing.py gui/tests/test_browser_clamav.py gui/tests/test_dashboard_runtime_status_lines.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_bulk_ops.py gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:
- Yes (final sign-off).

---

## Prompt Seed (Generic)

```text
Implement Card C{N} from docs/dev/modularization_15Apr/TASK_CARDS.md.

Constraints:
- Preserve existing SMB/FTP/HTTP behavior.
- Keep edits minimal and reversible.
- Maintain legacy import compatibility.
- No commits.

Deliver:
- Issue / Root cause / Fix summary
- Files changed
- Exact validation commands + PASS/FAIL
- Risks/assumptions
- HI manual test checklist
```
