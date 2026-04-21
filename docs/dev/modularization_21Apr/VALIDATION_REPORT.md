# Validation Report (21 Apr Modularization)

## Commands Run

```bash
python3 -m py_compile shared/database.py shared/database_ftp_persistence.py shared/database_http_persistence.py
./venv/bin/python -m pytest shared/tests/test_ftp_state_tables.py shared/tests/test_http_endpoint_identity.py shared/tests/test_ftp_operation.py shared/tests/test_http_operation.py -q

python3 -m py_compile gui/dashboard/widget.py gui/dashboard/scan_controls.py
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_bulk_ops.py gui/tests/test_dashboard_reddit_wiring.py gui/tests/test_dashboard_api_key_gate.py gui/tests/test_dashboard_runtime_status_lines.py -q

python3 -m py_compile gui/tests/test_db_tools_engine.py gui/tests/test_db_tools_engine_schema_preview.py gui/tests/test_db_tools_engine_merge.py gui/tests/test_db_tools_engine_maintenance.py gui/tests/test_db_tools_engine_csv.py
./venv/bin/python -m pytest gui/tests/test_db_tools_engine*.py -q

python3 -m py_compile gui/components/scan_dialog.py gui/components/scan_dialog_layout.py gui/components/ftp_scan_dialog.py gui/components/http_scan_dialog.py gui/components/unified_scan_dialog.py
./venv/bin/python -m pytest gui/tests/test_scan_dialog_nonblocking_singleton.py gui/tests/test_ftp_scan_dialog.py gui/tests/test_unified_scan_dialog_validation.py -q

python3 -m py_compile gui/utils/db_tools_engine.py gui/utils/db_tools_engine_core_methods.py gui/utils/db_tools_engine_merge_methods.py gui/utils/db_tools_engine_maintenance_methods.py
./venv/bin/python -m pytest gui/tests/test_db_tools_engine*.py -q

python3 -m py_compile gui/utils/database_access.py gui/utils/database_access_core_methods.py gui/utils/database_access_write_methods.py gui/utils/database_access_protocol_methods.py
./venv/bin/python -m pytest gui/tests/test_database_access_protocol_union.py gui/tests/test_database_access_protocol_writes.py gui/tests/test_database_access_scan_cohort.py -q

./venv/bin/python -m pytest shared/tests/test_ftp_state_tables.py shared/tests/test_http_endpoint_identity.py shared/tests/test_ftp_operation.py shared/tests/test_http_operation.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_bulk_ops.py gui/tests/test_dashboard_reddit_wiring.py gui/tests/test_dashboard_api_key_gate.py gui/tests/test_dashboard_runtime_status_lines.py gui/tests/test_db_tools_engine*.py gui/tests/test_scan_dialog_nonblocking_singleton.py gui/tests/test_ftp_scan_dialog.py gui/tests/test_unified_scan_dialog_validation.py gui/tests/test_database_access_protocol_union.py gui/tests/test_database_access_protocol_writes.py gui/tests/test_database_access_scan_cohort.py -q

git ls-files '*.py' | xargs wc -l | sort -nr | head -n 15
```

## Results

- Targeted suites: PASS
- Aggregate suite: `248 passed`
- Global python line-count ceiling: PASS (`max = 1462`)
