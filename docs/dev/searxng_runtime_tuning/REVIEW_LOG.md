# SearXNG Runtime Tuning - Review Log

## Baseline

- Branch: `development`
- Sequential pipeline commit: `662d2a2`
- Service tests: 79 passed
- Desktop/WebUI integration tests: 131 passed
- Quick lane: 60 passed
- Worktree was clean before C11 planning files were added.

## Pre-C11A Line Counts (constrained files)

| File | Lines at baseline |
|---|---|
| `experimental/se_dork/service.py` | 1,085 |
| `gui/components/dashboard_scan.py` | 1,679 |
| `gui/dashboard/widget.py` | 1,678 |

## Post-C11B Line Counts

| File | Lines after C11B |
|---|---|
| `gui/components/unified_scan_dialog.py` | 1,175 |
| `gui/components/scan_provider_options.py` | 601 |
| `gui/components/unified_scan_layout.py` | 633 |
| `gui/components/dashboard_searxng_scan.py` | 399 |
| NEW `gui/tests/test_unified_scan_dialog_searxng_controls.py` | 387 |
| NEW `gui/tests/test_scan_provider_options_searxng_scales.py` | 157 |
| `experimental/se_dork/service.py` | 1,200 (unchanged) |

## Post-C11A Line Counts

| File | Lines after C11A |
|---|---|
| `experimental/se_dork/service.py` | 1,200 (≤ 1,200 ✓) |
| `gui/components/dashboard_scan.py` | 1,437 (≤ 1,700 ✓) |
| `gui/dashboard/widget.py` | 1,684 |
| NEW `gui/components/dashboard_searxng_scan.py` | 378 |
| `gui/components/dashboard_scan_rollup.py` | 290 |

## Review Checklist

- Confirm card scope before implementation.
- Review findings by severity.
- Check cancellation at every blocking or page-stage boundary.
- Confirm completed data survives cancellation.
- Confirm no primary sync runs for failed processing.
- Confirm file sizes before and after.
- Run targeted tests before wider regression.
- Review README and `docs/TECHNICAL_REFERENCE.md`.
- Do not commit until HI requests it.
