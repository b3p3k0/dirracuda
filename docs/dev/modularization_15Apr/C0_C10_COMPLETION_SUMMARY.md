# C0-C10 Modularization Completion Summary

Date: 2026-04-15  
Status: Completed (AUTOMATED PASS; MANUAL PASS reported by HI)

## Outcome

The C0-C10 modularization program completed with compatibility-preserving shims and no net regressions beyond the two pre-existing database-schema failures tracked since baseline.

## Delivered Work by Card

| Card | Delivered |
|---|---|
| C0 | Contract freeze and baseline evidence recorded in `BASELINE_CONTRACTS.md` |
| C1 | Shared utility consolidation for coercion and file-size formatting (`gui/utils/coercion.py`, `gui/utils/filesize.py`) |
| C2 | `gui/browsers/` package scaffold with compatibility re-exports and import-contract test coverage |
| C3 | `UnifiedBrowserCore` extracted to `gui/browsers/core.py` with compatibility wiring preserved |
| C4 | FTP/HTTP browser classes extracted to `gui/browsers/ftp_browser.py` and `gui/browsers/http_browser.py` |
| C5 | SMB browser + browser factory extracted to `gui/browsers/smb_browser.py` and `gui/browsers/factory.py` |
| C6 | Dashboard runtime-status/coercion composition extracted to `gui/components/dashboard_status.py` |
| C7 | Dashboard scan orchestration extracted to `gui/components/dashboard_scan.py` |
| C8 | Dashboard post-scan batch orchestration extracted to `gui/components/dashboard_batch_ops.py` |
| C9 | `gui/dashboard/` package created; `gui/components/dashboard.py` converted to compatibility shim |
| C10 | Final gate closeout validation and evidence capture appended to `BASELINE_CONTRACTS.md` Appendix C |

## Final Architecture

- `gui/components/dashboard.py` is now a compatibility shim that re-exports `DashboardWidget` and keeps all patch-sensitive names at module scope.
- `gui/dashboard/widget.py` is the canonical `DashboardWidget` implementation.
- `gui/components/unified_browser_window.py` is now a compatibility shim.
- Browser implementations reside in `gui/browsers/` (`core`, `ftp_browser`, `http_browser`, `smb_browser`, `factory`).

## Compatibility/Contract Result

- Legacy imports remain valid (including production entrypoint usage).
- New package imports (`gui.dashboard`, `gui.browsers`) are valid.
- Frozen monkeypatch paths at `gui.components.dashboard.*` and `gui.components.unified_browser_window.*` were preserved.
- C9 shim interception regression (FTP/HTTP dialog wiring) was remediated and guardrail-tested.

## Final Automated Evidence (C10)

- Compile smoke: PASS
- Canonical import smoke: PASS
- Full suite: `1045 passed, 2 failed` (pre-existing only)
- Coverage (`shared` + `gui`): `54%` (`37,748` statements; `17,350` missed)
- Line counts:
  - `gui/components/dashboard.py`: `58` (from `3331`, -98%)
  - `gui/components/unified_browser_window.py`: `105` (from `3238`, -97%)
  - Combined: `163` (from `6569`, -98%)

## Known Pre-existing Failures (Out of C1-C10 Scope)

- `test_manual_upsert_inserts_smb_ftp_http_rows`
- `test_manual_upsert_http_same_ip_different_ports_create_distinct_rows`
- Root cause: `sqlite3.OperationalError: table http_servers has no column named probe_host`

## Primary Evidence Location

See `docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md` for:
- frozen contract inventories,
- baseline and final validation commands/results,
- change log with C10 entry,
- Appendix C final metrics and deltas.
