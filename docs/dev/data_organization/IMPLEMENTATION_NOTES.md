# Layout v2 Implementation Notes

Date: 2026-04-26

## What Was Implemented

- Added/expanded canonical path + migration service in `shared/path_service.py`.
- Switched runtime defaults to home-canonical config/DB/settings/template/log/cache locations.
- Added startup layout-v2 migration execution + notification flow in `dirracuda`.
- Updated probe/template/cache/path consumers to read canonical first and legacy fallback as needed.
- Updated config dialog defaults and browse roots for home-canonical layout.
- Updated README + technical reference paths to layout-v2 locations.

## Late Legacy DB Recovery Update

- Startup migration no longer treats `layout_version=2,status=success` as final when canonical DB is missing and a legacy DB exists.
- Added targeted main-DB recovery migration with backup/report/state updates for late-arriving legacy DBs.
- Runtime keeps strict persisted-path precedence, but now supports session-only DB fallback to an existing legacy DB when DB recovery is incomplete.
- GUI and CLI startup paths both run the same migration preflight and consume the same session fallback logic.

## Validation Commands

```bash
./venv/bin/python -m py_compile dirracuda shared/path_service.py shared/config.py shared/db_path_resolution.py shared/db_migrations.py shared/rce_scanner/logger.py gui/utils/settings_manager.py gui/utils/template_store.py gui/utils/probe_cache.py gui/utils/ftp_probe_cache.py gui/utils/http_probe_cache.py gui/utils/extract_runner.py gui/components/app_config_dialog.py

./venv/bin/python -m pytest shared/tests/test_db_path_resolution.py shared/tests/test_path_migration.py shared/tests/test_tmpfs_quarantine.py shared/tests/test_quarantine_promotion.py shared/tests/test_se_dork_store.py shared/tests/test_dorkbook_store.py gui/tests/test_app_config_dialog.py gui/tests/test_app_config_dialog_clamav.py gui/tests/test_app_config_dialog_tmpfs.py gui/tests/test_dashboard_runtime_status_lines.py gui/tests/test_db_path_sync_precedence.py gui/tests/test_db_unification.py gui/tests/test_ftp_scan_dialog.py gui/tests/test_config_save_reconciliation.py -q --tb=short

./venv/bin/python -m pytest shared/tests/test_signature_loader_paths.py shared/tests/test_verdict_conditions.py gui/tests/test_ftp_probe.py gui/tests/test_http_probe.py gui/tests/test_probe_cache_dispatch.py gui/tests/test_keymaster_window.py gui/tests/test_se_dork_browser_window.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py -q --tb=short

./venv/bin/python -m pytest gui/tests/test_dirracuda_db_unification_startup_ui.py gui/tests/test_dirracuda_tmpfs_warning_dialog_schedule.py gui/tests/test_legacy_gui_main_entrypoint.py gui/tests/test_dirracuda_close_behavior.py gui/tests/test_server_ops_scenario_matrix.py gui/tests/test_server_ops_fuzz_sequences.py -q --tb=short
```
