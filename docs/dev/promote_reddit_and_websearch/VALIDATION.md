# Validation and size audit

Date: 2026-09-27. No live network services were used. The full repository suite
was not run; validation targets desktop routing, provider behavior, retained
legacy browsing, and GUI guardrails.

## PASS — focused regression (307 tests)

```bash
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py gui/tests/test_experimental_features_dialog_geometry.py gui/tests/test_unified_scan_dialog.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_scan.py gui/tests/test_dashboard_provider_queue.py gui/tests/test_dashboard_widget.py gui/tests/test_dashboard_database.py gui/tests/test_dashboard_experimental_sidecar.py gui/tests/test_reddit_browser_window.py gui/tests/test_se_dork_browser_window.py gui/tests/test_messagebox_guardrail.py gui/tests/test_theme_style_guardrail.py -q
```

## PASS — additional GUI checks under Xvfb (281 tests)

```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog_layout.py gui/tests/test_unified_scan_dialog_validation.py gui/tests/test_unified_scan_dialog_searxng_controls.py gui/tests/test_scan_provider_options_searxng_scales.py gui/tests/test_scan_dialog_nonblocking_singleton.py gui/tests/test_dashboard_searxng_cancel.py gui/tests/test_scan_results_dialog.py gui/tests/test_sherlock_unsaved_changes.py gui/tests/test_sherlock_tab.py -q
```

## Intermediate checks

The baseline command in [README](README.md#validation) passed 205 tests.
After removal, this command first returned 16 failures/169 passes, then passed
all 185 after restoring a shared test helper and correcting a tab-order test:

```bash
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py gui/tests/test_unified_scan_dialog.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_scan.py gui/tests/test_dashboard_provider_queue.py gui/tests/test_dashboard_widget.py -q
```

The intermediate failures were test cleanup mistakes, not provider runtime
failures. The later 307-test run includes this coverage.

## Static and documentation review

- PASS: changed Python files parsed with `ast.parse`.
- PASS: `git diff --check`.
- PASS: caller search found no production references to deleted desktop tabs,
  Reddit Grab dialog, or retired browser launchers.
- Reviewed README against retained Start New Scan controls and main-DB flow.
  Removed obsolete accessory screenshots/instructions, corrected blank search
  query default, and separated discovery docs from Accessories.
- Shared services, schemas, persisted config keys, and Web UI code are unchanged.
- Manual desktop/live-provider validation remains with HI; steps are in README.

## File sizes

Counts are physical lines, before → after. All touched production code is below
1,700 lines. Existing test-size debt is explicitly deferred per HI's direction:
`test_experimental_features_dialog.py` shrinks by 396 lines; the 2,749-line
Sherlock test file changes only its stale docstring reference to a deleted test.
Splitting unrelated test groups would broaden this task. Neither test file grew.

| File | Before | After | Grade after |
|---|---:|---:|---|
| `AGENTS.md` | 167 | 170 | excellent |
| `README.md` | 797 | 790 | excellent |
| `docs/SELF_HOSTED_SEARCH.md` | 107 | 107 | excellent |
| `docs/TECHNICAL_REFERENCE.md` | 1656 | 1654 | acceptable |
| `gui/components/dashboard.py` | 59 | 57 | excellent |
| `gui/components/dashboard_experimental.py` | 430 | 395 | excellent |
| `gui/components/dashboard_provider_queue.py` | 407 | 406 | excellent |
| `gui/components/dashboard_scan.py` | 1437 | 1436 | good |
| `gui/components/experimental_features/reddit_tab.py` | 132 | 0 | removed |
| `gui/components/experimental_features/registry.py` | 82 | 70 | excellent |
| `gui/components/experimental_features/se_dork_tab.py` | 447 | 0 | removed |
| `gui/components/experimental_features_dialog.py` | 163 | 161 | excellent |
| `gui/components/reddit_grab_dialog.py` | 392 | 0 | removed |
| `gui/components/unified_scan_dialog.py` | 1210 | 1197 | excellent |
| `gui/dashboard/scan_controls.py` | 563 | 377 | excellent |
| `gui/dashboard/widget.py` | 1388 | 1380 | good |
| `gui/tests/test_dashboard_provider_queue.py` | 313 | 313 | excellent |
| `gui/tests/test_dashboard_reddit_wiring.py` | 638 | 0 | removed |
| `gui/tests/test_dashboard_scan.py` | 1266 | 1255 | good |
| `gui/tests/test_dashboard_widget.py` | 68 | 67 | excellent |
| `gui/tests/test_experimental_features_dialog.py` | 2347 | 1951 | poor |
| `gui/tests/test_reddit_grab_dialog.py` | 416 | 0 | removed |
| `gui/tests/test_se_dork_tab.py` | 626 | 0 | removed |
| `gui/tests/test_sherlock_tab.py` | 2749 | 2749 | unacceptable; deferred debt |
| `CLAUDE.md` (local, ignored) | 153 | 163 | excellent |
| `docs/dev/promote_reddit_and_websearch/README.md` | 0 | 82 | excellent |
| `docs/dev/promote_reddit_and_websearch/LESSONS_LEARNED.md` | 0 | 20 | excellent |
| `docs/dev/promote_reddit_and_websearch/VALIDATION.md` | 0 | 81 | excellent |
